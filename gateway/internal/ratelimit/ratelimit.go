package ratelimit

import (
	"net/http"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

// keyLimiter 按用户/租户/IP 维度做令牌桶限流。
// 多副本部署需改用 Redis 令牌桶以保证全局一致。
type keyLimiter struct {
	mu       sync.Mutex
	limiters map[string]*rate.Limiter
	lastSeen map[string]time.Time
	rps      rate.Limit
	burst    int
}

func newKeyLimiter(rps float64, burst int) *keyLimiter {
	return &keyLimiter{
		limiters: make(map[string]*rate.Limiter),
		lastSeen: make(map[string]time.Time),
		rps:      rate.Limit(rps),
		burst:    burst,
	}
}

func (k *keyLimiter) get(key string) *rate.Limiter {
	k.mu.Lock()
	defer k.mu.Unlock()

	now := time.Now()
	if l, ok := k.limiters[key]; ok {
		k.lastSeen[key] = now
		return l
	}
	// 防止内存无限增长：超过阈值时清理 10 分钟未活动的条目
	if len(k.limiters) > 5000 {
		for key, t := range k.lastSeen {
			if now.Sub(t) > 10*time.Minute {
				delete(k.limiters, key)
				delete(k.lastSeen, key)
			}
		}
	}
	l := rate.NewLimiter(k.rps, k.burst)
	k.limiters[key] = l
	k.lastSeen[key] = now
	return l
}

// Wrap 按身份限流（优先 X-User-Id，其次 X-Tenant-Id，最后回退客户端 IP），
// 避免单个用户刷接口；未登录请求按 IP 限速。
// 需在鉴权之后调用，以拿到网关注入的 X-User-* 身份头。
func Wrap(next http.Handler, rps float64, burst int) http.Handler {
	if rps <= 0 {
		return next
	}
	kl := newKeyLimiter(rps, burst)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := r.Header.Get("X-User-Id")
		if key == "" {
			key = r.Header.Get("X-Tenant-Id")
		}
		if key == "" {
			key = r.RemoteAddr
		}
		if !kl.get(key).Allow() {
			http.Error(w, `{"error":"too many requests"}`, 429)
			return
		}
		next.ServeHTTP(w, r)
	})
}
