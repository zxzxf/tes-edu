package middleware

import (
	"net/http"

	"github.com/gin-gonic/gin"
	"golang.org/x/time/rate"
)

// RateLimit 全局令牌桶限流（P0 够用；生产可改为按 IP/用户细分）
func RateLimit(rps float64) gin.HandlerFunc {
	l := rate.NewLimiter(rate.Limit(rps), int(rps)+5)
	return func(c *gin.Context) {
		if !l.Allow() {
			c.AbortWithStatusJSON(http.StatusTooManyRequests, gin.H{"error": "rate limited"})
			return
		}
		c.Next()
	}
}
