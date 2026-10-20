package router

import (
	"net/http/httputil"
	"net/url"
	"strings"

	"tes.edu/gateway/internal/config"
	"tes.edu/gateway/internal/middleware"

	"github.com/gin-gonic/gin"
)

// New 构建网关引擎：审计 + 限流（全局）-> 鉴权 -> 按路径反向代理到后端
func New(cfg *config.Config) *gin.Engine {
	r := gin.Default()
	r.Use(middleware.Audit())
	r.Use(middleware.RateLimit(cfg.RateLimit))

	// 健康检查无需鉴权
	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(200, gin.H{"status": "ok"})
	})

	// 公开路由：登录接口无需鉴权（登录后才能拿到 token）
	r.POST("/api/admin/login", proxy("/api/admin", cfg.WebBackendURL))

	// 业务路由：统一鉴权后按前缀分流到不同后端
	authed := r.Group("/")
	authed.Use(middleware.Auth(cfg.JWTSecret))
	{
		// C 端：学生 App 走 Agent 后端
		authed.Any("/api/app/*path", proxy("/api/app", cfg.AgentBackendURL))
		// B 端：Web 管理后台走 Web 后端（web-backend:8001）
		authed.Any("/api/admin/*path", proxy("/api/admin", cfg.WebBackendURL))
	}
	return r
}

// proxy 将请求反代到 target，并去掉 prefix 前缀（如 /api/app/solve -> /solve）
func proxy(prefix, target string) gin.HandlerFunc {
	return func(c *gin.Context) {
		u, err := url.Parse(target)
		if err != nil {
			c.JSON(502, gin.H{"error": "bad upstream"})
			return
		}
		c.Request.URL.Path = strings.TrimPrefix(c.Request.URL.Path, prefix)
		if c.Request.URL.Path == "" {
			c.Request.URL.Path = "/"
		}
		httputil.NewSingleHostReverseProxy(u).ServeHTTP(c.Writer, c.Request)
	}
}
