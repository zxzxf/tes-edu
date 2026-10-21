package main

import (
	"context"
	"encoding/json"
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

	"gateway/internal/allowlist"
	"gateway/internal/audit"
	"gateway/internal/auth"
	"gateway/internal/breaker"
	"gateway/internal/compliance"
	"gateway/internal/config"
	"gateway/internal/license"
	"gateway/internal/metrics"
	"gateway/internal/ratelimit"
	"gateway/internal/telemetry"
)

// RBACRoles 允许访问 /api/admin/* 的 B 端角色白名单
// 实际以路由配置中的 roles 为准，这里作为默认兜底
var RBACRoles = []string{"admin", "teacher", "researcher", "operator"}

// buildHandler 构造完整网关节点：路由中间件链（audit→auth→ratelimit→compliance→
// allowlist→breaker→proxy）、授权心跳、匿名指标回传、运维端点（/healthz、
// /licensez、/metricsz），以及启动时审计日志保留期清理。
//
// 抽成独立函数是为了让集成测试复用真实的接线逻辑（main 本身不可测），
// 避免把中间件链在测试里重写一遍导致"测试与线上不一致"。
//
// 返回：mux（已注册全部路由）、授权心跳器（可能为 nil）、cleanup（停止后台
// goroutine 并关闭 Redis 连接，退出时必须调用）。
func buildHandler(cfg *config.Config, ctx context.Context) (mux *http.ServeMux, hb *license.Heartbeat, cleanup func()) {
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
			log.Printf("warn: redis ping failed: %v（限流回退到本地桶）", err)
			redisClient = nil
		} else {
			log.Printf("redis connected: %s", cfg.Redis.Addr)
		}
	}

	mux = http.NewServeMux()

	// 熔断器按上游共享：多个路由指向同一后端时共享熔断态，
	// 避免一个路由把后端打挂后其他路由还在继续锤。
	breakers := make(map[string]*breaker.Breaker)

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

		// 该上游的熔断器（懒加载 + 共享）
		bc := cfg.BreakerFor(r.Upstream)
		br, exists := breakers[r.Upstream]
		if !exists {
			if bc.Enabled {
				br = breaker.New(breaker.Options{
					Name:             r.Upstream,
					FailureThreshold: bc.FailureThreshold,
					Cooldown:         time.Duration(bc.CooldownSeconds) * time.Second,
				})
				log.Printf("breaker enabled for upstream %q: threshold=%d cooldown=%ds",
					r.Upstream, bc.FailureThreshold, bc.CooldownSeconds)
			} else {
				br = nil // 显式关闭：不包熔断层
				log.Printf("breaker disabled for upstream %q", r.Upstream)
			}
			breakers[r.Upstream] = br
		}

		proxy := httputil.NewSingleHostReverseProxy(target)

		// 上游超时与长连接池调优（防雪崩核心）。
		// ResponseHeaderTimeout=0：不截断 LLM 流式回答（与 WriteTimeout=0 同理）。
		proxy.Transport = &http.Transport{
			DialContext: (&net.Dialer{
				Timeout:   3 * time.Second, // TCP 建连超时
				KeepAlive: 30 * time.Second,
			}).DialContext,
			MaxIdleConns:          100,             // 全局最大空闲连接
			MaxIdleConnsPerHost:   20,              // 每个上游最大空闲连接
			IdleConnTimeout:       90 * time.Second, // 空闲连接回收
			TLSHandshakeTimeout:   5 * time.Second,
			ExpectContinueTimeout: 1 * time.Second,
			ResponseHeaderTimeout: 0,
		}

		// 上游出错时不暴露后端细节，返回统一网关错误
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			log.Printf("upstream error: %s %s -> %v", r.Method, r.URL.Path, err)
			http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
		}

		handler := http.Handler(proxy)

		// 熔断（最靠近代理的一层）：统计真实上游结果；
		// 打开时直接 503 + Retry-After，保护僵死后端不被继续打
		if br != nil {
			handler = breaker.Wrap(handler, br, 500)
		}

		// LLM endpoint 白名单（P5 合同硬要求：白名单外 /v1 端点一律 403）。
		// 位于鉴权之后：拒绝日志能带上 uid；位于转发之前：白名单外请求不打上游。
		// 未带 X-LLM-Endpoint 头的请求默认放行（非 LLM 流量不受影响）。
		if cfg.LLMAllowlist.Enabled {
			entries, err := allowlist.ParseEndpoints(cfg.LLMAllowlist.Endpoints)
			if err != nil {
				log.Fatalf("llm_allowlist endpoints: %v", err)
			}
			handler = allowlist.Wrap(handler, allowlist.Options{
				Enabled:       true,
				Header:        cfg.LLMAllowlist.Header,
				RequireHeader: cfg.LLMAllowlist.RequireHeader,
				Entries:       entries,
			})
			log.Printf("llm allowlist enabled: %d endpoint(s), header=%s require_header=%v",
				len(entries), cfg.LLMAllowlist.Header, cfg.LLMAllowlist.RequireHeader)
		}

		// 合规（输入校验 + 内容安全 + 敏感操作审计），位于鉴权之后、转发之前。
		// 内容安全：JSON/文本请求体敏感词扫描，命中即 403（未成年人保护）；
		// multipart（拍照上传）只做 15MB 大小闸门，文本深审由后端负责。
		handler = compliance.Wrap(handler, compliance.Options{
			ContentSafetyEnabled: cfg.ContentSafety.Enabled,
			Keywords:             cfg.ContentSafety.Keywords,
			MaxScanBytes:         cfg.ContentSafety.MaxScanBytes,
		})
		if cfg.ContentSafety.Enabled {
			log.Printf("content safety enabled: %d keyword(s), max_scan=%dB",
				len(cfg.ContentSafety.Keywords), cfg.ContentSafety.MaxScanBytes)
		}

		// 限流（按身份/租户/IP），位于鉴权之后，可识别调用者做精准限速
		handler = ratelimit.Wrap(handler, ratelimit.Options{
			RPS:          r.Rate,
			Burst:        r.Burst,
			RouteKey:     r.Match,
			RedisEnabled: cfg.Redis.Enabled,
			RedisClient:  redisClient,
			KeyPrefix:    cfg.Redis.KeyPrefix,
		})

		// 鉴权（按路由 mode），最外层，负责校验 JWT 并注入 X-User-* 身份头。
		// RequireTenant：多租户强制隔离——该路由要求 JWT 带非空 tenant_id，否则 403，
		// 防止"无租户令牌"跨空间访问（B 端/机构路由必须开启）。
		roles := r.Roles
		if len(roles) == 0 {
			roles = RBACRoles
		}
		handler = auth.Wrap(handler, auth.Options{
			Mode:          r.Mode,
			Secret:        cfg.JWTSecret,
			Roles:         roles,
			RequireTenant: r.RequireTenant,
		})

		mux.Handle(r.Match, handler)
	}

	// 健康检查（不鉴权、不限流）
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		_, _ = w.Write([]byte("ok"))
	})

	// 运维观测端点（仅本机/内网可访问，鉴权由 isLocalRequest 把关）：
	//   /licensez —— 授权心跳健康状态（ok/degraded/disabled）
	//   /metricsz —— 匿名计数器快照（telemetry 回传的同源数据）
	mux.HandleFunc("/licensez", func(w http.ResponseWriter, r *http.Request) {
		if !isLocalRequest(r) {
			http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if hb == nil {
			_, _ = w.Write([]byte(`{"state":"disabled"}`))
			return
		}
		_ = json.NewEncoder(w).Encode(hb.Status())
	})

	mux.HandleFunc("/metricsz", func(w http.ResponseWriter, r *http.Request) {
		if !isLocalRequest(r) {
			http.Error(w, `{"error":"forbidden"}`, http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"instance_id": cfg.InstanceID,
			"version":     cfg.Version,
			"counters":    metrics.Snapshot(),
		})
	})

	// 授权心跳（P5 合同硬要求 2/3：授权/更新回连，心跳 ≤100KB/天）。
	// 纯旁路：失败只记日志，绝不影响请求转发；状态经 /licensez 暴露。
	if cfg.License.Enabled {
		if cfg.License.URL == "" {
			log.Fatalf("license.enabled=true 但 license.url 为空：心跳无接收端，请配置 LICENSE_URL")
		}
		hb = license.New(license.Options{
			Enabled:    true,
			URL:        cfg.License.URL,
			Interval:   time.Duration(cfg.License.IntervalSeconds) * time.Second,
			InstanceID: cfg.InstanceID,
			Version:    cfg.Version,
		})
		hb.Start(ctx)
		log.Printf("license heartbeat enabled: url=%s interval=%ds instance=%s",
			cfg.License.URL, cfg.License.IntervalSeconds, cfg.InstanceID)
	}

	// 匿名指标回传（P5 合同硬要求 3/3：仅指标与错误码，不含学生内容）。
	// 回传字段白名单由 metrics 包常量强制；失败只记日志。
	if cfg.Telemetry.Enabled {
		if cfg.Telemetry.URL == "" {
			log.Fatalf("telemetry.enabled=true 但 telemetry.url 为空：请配置 TELEMETRY_URL")
		}
		rep := telemetry.New(telemetry.Options{
			Enabled:    true,
			URL:        cfg.Telemetry.URL,
			Interval:   time.Duration(cfg.Telemetry.IntervalSeconds) * time.Second,
			InstanceID: cfg.InstanceID,
			Version:    cfg.Version,
		})
		rep.Start(ctx)
		log.Printf("telemetry enabled: url=%s interval=%ds", cfg.Telemetry.URL, cfg.Telemetry.IntervalSeconds)
	}

	// 审计日志保留期清理（数据留存策略的网关侧落地）：
	// 启动时清理同目录超期历史日志（access.log.YYYYMMDD 等轮转产物），
	// retention<=0 表示不启用保留期，不删任何文件。
	if cfg.AuditRetentionDays > 0 {
		if deleted := audit.CleanupOldLogs(cfg.AuditLog, cfg.AuditRetentionDays); len(deleted) > 0 {
			log.Printf("audit log cleanup: removed %d expired file(s) (retention=%dd)",
				len(deleted), cfg.AuditRetentionDays)
		}
	}

	cleanup = func() {
		if hb != nil {
			hb.Stop() // 等后台心跳 goroutine 退出，避免退出窗口内残留上报
		}
		if redisClient != nil {
			_ = redisClient.Close()
		}
	}
	return mux, hb, cleanup
}

func main() {
	cfgPath := "config.yaml"
	if v := os.Getenv("CONFIG_PATH"); v != "" {
		cfgPath = v
	}
	cfg, err := config.Load(cfgPath)
	if err != nil {
		log.Fatalf("load config %s: %v", cfgPath, err)
	}

	// 优雅退出上下文：授权心跳/指标回传等后台 goroutine 共用同一 ctx，
	// SIGTERM 时统一停发（避免退出窗口内心跳打到正在下线的旧实例）。
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	mux, _, cleanup := buildHandler(cfg, ctx)
	defer cleanup()

	// 审计日志在最外层；返回的 closer 在优雅退出时释放文件句柄
	muxHandler, auditCloser := audit.Log(mux, cfg.AuditLog)

	// 用带超时的 http.Server 替换 ListenAndServe，配合 Shutdown 做优雅退出。
	// WriteTimeout 显式置 0（不限）：LLM 流式输出（SSE）可能持续数十秒，
	// 设限会截断长回答；防慢连接靠 ReadHeaderTimeout/ReadTimeout，
	// 防半成品长连接堆积靠 IdleTimeout，防后端僵死靠 Transport 超时。
	srv := &http.Server{
		Addr:              cfg.Listen,
		Handler:           muxHandler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      0, // 允许流式长响应（SSE），不在此处截断
		IdleTimeout:       120 * time.Second,
	}

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
		if err := auditCloser.Close(); err != nil {
			log.Printf("warn: audit log close: %v", err)
		}
		log.Printf("gateway stopped")
	}
}

// isLocalRequest 判断请求是否来自本机或内网（/licensez、/metricsz 仅限运维访问）。
// 容器化部署时 RemoteAddr 是 ingress/边车 IP，落在私网段即放行。
func isLocalRequest(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	return ip.IsLoopback() || ip.IsPrivate()
}
