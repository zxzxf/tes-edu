package middleware

import (
	"log"
	"time"

	"github.com/gin-gonic/gin"
)

// Audit 访问审计中间件（合规 #8 落点：谁、何时、访问了哪个接口、结果如何）
func Audit() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		uid, _ := c.Get("uid")
		role, _ := c.Get("role")
		log.Printf("[AUDIT] %s %s -> %d | uid=%v role=%v | %v",
			c.ClientIP(), c.Request.URL.Path, c.Writer.Status(), uid, role, time.Since(start))
	}
}
