package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"

	"gateway/internal/audit"
	"gateway/internal/auth"
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
		handler := http.Handler(proxy)

		// 鉴权（按路由 mode）
		roles := r.Roles
		if len(roles) == 0 {
			roles = RBACRoles
		}
		handler = auth.Wrap(handler, r.Mode, cfg.JWTSecret, roles)

		// 限流（按路由 rate/burst）
		handler = ratelimit.Wrap(handler, r.Rate, r.Burst)

		mux.Handle(r.Match, handler)
	}

	// 健康检查（不鉴权、不限流）
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		_, _ = w.Write([]byte("ok"))
	})

	// 审计日志在最外层
	handler := audit.Log(mux, cfg.AuditLog)

	log.Printf("gateway listening on %s, routes=%d", cfg.Listen, len(cfg.Routes))
	if err := http.ListenAndServe(cfg.Listen, handler); err != nil {
		log.Fatalf("listen: %v", err)
	}
}
