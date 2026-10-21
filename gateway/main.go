package main

import (
	"context"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/redis/go-redis/v9"

	"gateway/internal/audit"
	"gateway/internal/auth"
	"gateway/internal/compliance"
	"gateway/internal/config"
	"gateway/internal/ratelimit"
)

// RBACRoles 允许访问 /api/admin/* 的 B 端角色白名单
// 实际以路由配置中的 roles 为准，这里作为默认兜底
var RBACRoles = []string{"admin", "teacher", "researcher", "operator"}

func main() {
	cfgPath := "config.yaml"
	if v := os.Getenv("CONFIG_PATH"); v != "" {
		cfgPath = v
	}
	cfg, err := config.Load(cfgPath)
	if err != nil {
		log.Fatalf("load config %s: %v", cfgPath, err)
	}

	// 限流用的 Redis 客户端（多副本全局令牌桶）。未启用或连接失败时不致命，
	// 限流会回退到进程内本地桶。
	var redisClient *redis.Client
	if cfg.Redis.Enabled {
		redisClient = redis.NewClient(&redis.Options{
			Addr:         cfg.Redis.Addr,
			Password:     cfg.Redis.Password,
			DB:           cfg.Redis.DB,
			DialTimeout:  time.Duration(cfg.Redis.DialTimeout) * time.Millisecond,
			ReadTimeout:  time.Duration(cfg.Redis.ReadTimeout) * time.Millisecond,
			WriteTimeout: time.Duration(cfg.Redis.WriteTimeout) * time.Millisecond,
		})
		if err := redisClient.Ping(context.Background()).Err(); err != nil {
			// 连接失败：放弃 Redis，限流回退到进程内本地桶（可用性优先）
			log.Printf("warn: redis ping failed: %v（限流回退到本地桶）", err)
			redisClient = nil
		} else {
			log.Printf("redis connected: %s", cfg.Redis.Addr)
		}
	}

	mux := http.NewServeMux()

	for _, r := range cfg.Routes {
		up, ok := cfg.UpstreamURL(r.Upstream)
		if !ok {
			log.Printf("warn: route %s -> unknown upstream %q, skip", r.Match, r.Upstream)
			continue
		}
		target, err := url.Parse(up)
		if err != nil {
			log.Printf("warn: bad upstream url %q: %v", up, err)
			continue
		}

		proxy := httputil.NewSingleHostReverseProxy(target)

		// 上游超时与长连接池调优（防雪崩核心）。
		// 没有这组配置时：单个后端僵死 -> 连接无上限堆积 -> 耗尽 goroutine/端口 -> 整个网关不可用。
		// ResponseHeaderTimeout 只约束「拿到响应头」的时间，
		// 之后 body 的持续传输（LLM SSE 流式回答）不受影响。
		proxy.Transport = &http.Transport{
			DialContext: (&net.Dialer{
				Timeout:   3 * time.Second,  // TCP 建连超时
				KeepAlive: 30 * time.Second,
			}).DialContext,
			MaxIdleConns:          100,              // 全局最大空闲连接
			MaxIdleConnsPerHost:   20,               // 每个上游最大空闲连接
			IdleConnTimeout:       90 * time.Second, // 空闲连接回收
			TLSHandshakeTimeout:   5 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			ResponseHeaderTimeout: 0, // 与 WriteTimeout=0 同理：不截断 LLM 流式回答
		}

		// 上游出错时不暴露后端细节，返回统一网关错误
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			log.Printf("upstream error: %s %s -> %v", r.Method, r.URL.Path, err)
			http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
		}

		handler := http.Handler(proxy)

		// 合规（骨架：输入校验 + 敏感操作审计），位于鉴权之后、转发之前
		handler = compliance.Wrap(handler, nil)

		// 限流（按身份/租户/IP），位于鉴权之后，可识别调用者做精准限速
		handler = ratelimit.Wrap(handler, ratelimit.Options{
			RPS:          r.Rate,
			Burst:        r.Burst,
			RouteKey:     r.Match,
			RedisEnabled: cfg.Redis.Enabled,
			RedisClient:  redisClient,
			KeyPrefix:    cfg.Redis.KeyPrefix,
		})

		// 鉴权（按路由 mode），最外层，负责校验 JWT 并注入 X-User-* 身份头
		roles := r.Roles
		if len(roles) == 0 {
			roles = RBACRoles
		}
		handler = auth.Wrap(handler, r.Mode, cfg.JWTSecret, roles)

		mux.Handle(r.Match, handler)
	}

	// 健康检查（不鉴权、不限流）
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		_, _ = w.Write([]byte("ok"))
	})

	// 审计日志在最外层；返回的 closer 在优雅退出时释放文件句柄
	muxHandler, auditCloser := audit.Log(mux, cfg.AuditLog)

	// --- 用带超时的 http.Server 替换 ListenAndServe，配合 Shutdown 做优雅退出 ---
	//
	// 关键取舍：WriteTimeout 显式置 0（不限）。
	// 理由：LLM 讲解/引导式对话是流式输出（SSE），单请求可能持续数十秒；
	// 若设 WriteTimeout 会截断长回答。防慢连接靠 ReadHeaderTimeout/ReadTimeout，
	// 防半成品长连接堆积靠 IdleTimeout，防后端僵死靠 Transport 的 Dial/ResponseHeader 超时。
	srv := &http.Server{
		Addr:              cfg.Listen,
		Handler:           muxHandler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      0, // 允许流式长响应（SSE），不在此处截断
		IdleTimeout:       120 * time.Second,
	}

	// 优雅退出：收到 SIGTERM/SIGINT 后停止接收新连接，等待在途请求处理完（最多 15s）。
	// 滚动更新 / docker compose stop 时避免切断进行中的 LLM 回答。
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		log.Printf("gateway listening on %s, routes=%d", cfg.Listen, len(cfg.Routes))
		errCh <- srv.ListenAndServe()
	}()

	select {
	case err := <-errCh:
		if err != nil && err != http.ErrServerClosed {
			log.Fatalf("listen: %v", err)
		}
	case <-ctx.Done():
		log.Printf("shutdown signal received, draining...")
		stop()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdownCtx); err != nil {
			log.Printf("warn: graceful shutdown failed: %v", err)
		}
		if redisClient != nil {
			if err := redisClient.Close(); err != nil {
				log.Printf("warn: redis close: %v", err)
			}
		}
		if err := auditCloser.Close(); err != nil {
			log.Printf("warn: audit log close: %v", err)
		}
		log.Printf("gateway stopped")
	}
}
