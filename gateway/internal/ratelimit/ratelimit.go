package ratelimit

import (
	"net/http"

	"golang.org/x/time/rate"
)

// Wrap 单实例令牌桶限流。多副本部署需改用 Redis 令牌桶。
func Wrap(next http.Handler, rps float64, burst int) http.Handler {
	if rps <= 0 {
		return next
	}
	limiter := rate.NewLimiter(rate.Limit(rps), burst)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !limiter.Allow() {
			http.Error(w, `{"error":"too many requests"}`, 429)
			return
		}
		next.ServeHTTP(w, r)
	})
}
