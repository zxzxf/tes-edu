package main

import (
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"

	"gateway/internal/audit"
	"gateway/internal/auth"
	"gateway/internal/config"
	"gateway/internal/ratelimit"
)

// RBACRoles 允许访问 /api/admin/* 的角色白名单
var RBACRoles = []string{"admin", "teacher", "researcher", "operator"}

func main() {
	cfg, err := config.Load("config.yaml")
	if err != nil {
		log.Fatalf("load config: %v", err)
	}

	mux := http.NewServeMux()

	for _, r := range cfg.Routes {
		up, ok := cfg.UpstreamURL(r.Upstream)
		if !ok {
			log.Printf("warn: route %s -> unknown upstream %s, skip", r.Match, r.Upstream)
			continue
		}
		target, err := url.Parse(up)
		if err != nil {
			log.Printf("warn: bad upstream url %s: %v", up, err)
			continue
		}
		proxy := httputil.NewSingleHostReverseProxy(target)
		// 透传原始 Host 之外的必要头，并保留路径前缀
		handler := auth.Wrap(proxy, r.Auth, cfg.JWT.Secret, RBACRoles)
		handler = ratelimit.Wrap(handler, cfg.RateLimit.RPS, cfg.RateLimit.Burst)
		// 前缀匹配：/api/app/* 等
		mux.Handle(r.Match, stripOrKeep(r.Match, handler))
	}

	// 健康检查（不鉴权）
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		w.Write([]byte("ok"))
	})

	handler := audit.Log(mux, cfg.AuditLog)
	log.Printf("gateway listening on %s, routes=%d", cfg.Listen, len(cfg.Routes))
	if err := http.ListenAndServe(cfg.Listen, handler); err != nil {
		log.Fatalf("listen: %v", err)
	}
}

// stripOrKeep 保持前缀转发（Agent/Admin 后端通常期望带前缀）
// 如需剥离前缀再转发，改这里即可。
func stripOrKeep(prefix string, h http.Handler) http.Handler {
	return h
}
