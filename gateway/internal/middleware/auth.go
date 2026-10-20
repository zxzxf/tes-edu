package middleware

import (
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// Claims JWT 载荷：用户 ID 与角色（RBAC 四角色之一）
type Claims struct {
	UserID string `json:"uid"`
	Role   string `json:"role"` // student | teacher | researcher | parent | admin
	jwt.RegisteredClaims
}

// Auth 校验 Bearer Token，并把 uid/role 注入 gin context
func Auth(secret string) gin.HandlerFunc {
	return func(c *gin.Context) {
		h := c.GetHeader("Authorization")
		if !strings.HasPrefix(h, "Bearer ") {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing token"})
			return
		}
		tok := strings.TrimPrefix(h, "Bearer ")
		claims := &Claims{}
		if _, err := jwt.ParseWithClaims(tok, claims, func(t *jwt.Token) (interface{}, error) {
			return []byte(secret), nil
		}); err != nil {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid token"})
			return
		}
		c.Set("uid", claims.UserID)
		c.Set("role", claims.Role)
		// 把已校验的身份注入请求头，随反向代理透传给后端
		// （覆盖客户端可能伪造的同名头，避免越权）
		c.Request.Header.Set("X-User-Id", claims.UserID)
		c.Request.Header.Set("X-User-Role", claims.Role)
		c.Next()
	}
}
