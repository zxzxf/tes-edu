package ratelimit

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"golang.org/x/time/rate"
)

// redisScript 原子令牌桶（Lua）：检查并扣减，保证多副本部署下全局一致。
//   - KEYS[1] : 限流键 = keyPrefix + routeKey + ":" + 身份维度
//   - ARGV[1] : 桶容量(burst)   ARGV[2] :  refill 速率(令牌/秒)
//   - ARGV[3] : 当前毫秒时间戳   ARGV[4] : 本次消耗(=1)
//   - ARGV[5] : key 过期毫秒（容量/速率*2 + 1s，避免无限驻留）
//
// 返回 1 表示放行，0 表示限流。整个判断+扣减在单条 Lua 中原子完成。
var redisScript = redis.NewScript(`
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  ts = now
else
  local refill = rate * (now - ts) / 1000.0
  tokens = math.min(capacity, tokens + refill)
end

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, ttl)
return allowed
`)

// Options 限流选项。
// RedisEnabled=true 且 RedisClient 非 nil 时使用 Redis 全局令牌桶（多副本一致）；
// 否则回退到进程内 per-key 令牌桶（单副本）。
type Options struct {
	RPS          float64
	Burst        int
	RouteKey     string // 路由标识（如 /api/app/），用于隔离不同路由的限流桶
	RedisEnabled bool
	RedisClient  *redis.Client
	KeyPrefix    string
}

// keyLimiter 进程内 per-key 令牌桶，单副本 / Redis 异常回退时使用。
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

// identityKey 取限流维度：优先 X-User-Id，其次 X-Tenant-Id，最后回退客户端 IP。
// 需在鉴权之后调用，以拿到网关注入的 X-User-* 身份头。
func identityKey(r *http.Request) string {
	if v := r.Header.Get("X-User-Id"); v != "" {
		return v
	}
	if v := r.Header.Get("X-Tenant-Id"); v != "" {
		return v
	}
	return r.RemoteAddr
}

// Wrap 构造限流中间件。
//   - rps<=0 时直接放行（不做限流）
//   - 多副本部署请开启 Redis：所有副本共享同一 Redis，令牌桶全局一致
//   - Redis 不可用时自动回退本地桶（可用性优先，限流精度降级）
func Wrap(next http.Handler, opt Options) http.Handler {
	if opt.RPS <= 0 {
		return next
	}
	kl := newKeyLimiter(opt.RPS, opt.Burst)
	useRedis := opt.RedisEnabled && opt.RedisClient != nil
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := identityKey(r)
		key := opt.RouteKey + ":" + id

		if useRedis {
			ok, err := allowRedis(r, opt.RedisClient, opt.KeyPrefix+key, opt.RPS, opt.Burst)
			if err != nil {
				// Redis 异常：回退本地桶，保证请求不被整体阻断（限流精度降级）
				if !kl.get(key).Allow() {
					http.Error(w, `{"error":"too many requests"}`, 429)
					return
				}
			} else if !ok {
				http.Error(w, `{"error":"too many requests"}`, 429)
				return
			}
			next.ServeHTTP(w, r)
			return
		}

		if !kl.get(key).Allow() {
			http.Error(w, `{"error":"too many requests"}`, 429)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// allowRedis 用原子 Lua 脚本检查并扣减令牌，返回是否放行。
func allowRedis(r *http.Request, client *redis.Client, key string, rps float64, burst int) (bool, error) {
	ctx, cancel := context.WithTimeout(r.Context(), 200*time.Millisecond)
	defer cancel()
	now := time.Now().UnixMilli()
	ttl := int64((float64(burst)/rps)*2*1000) + 1000
	res, err := redisScript.Run(ctx, client, []string{key}, burst, rps, now, 1, ttl).Int64()
	if err != nil {
		return false, err
	}
	return res == 1, nil
}
