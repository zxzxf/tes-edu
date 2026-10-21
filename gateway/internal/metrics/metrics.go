// Package metrics 进程级匿名计数器（telemetry 回传的数据源）。
//
// 纪律（隐私红线）：
//   - 只允许 Inc/Add 预定义的无维度计数器键（见下方常量）；
//   - 绝不记录路径、uid、租户、题干、IP 等任何可能关联到个人的信息——
//     回传字段白名单 = 这些常量键，telemetry 只上报 Snapshot()。
package metrics

import (
	"sort"
	"sync"
)

// 预定义计数器键（白名单）。新增键必须加在这里并确认为匿名聚合值。
const (
	KeyRequests        = "requests"         // 总请求数
	KeyHTTP2xx         = "http_2xx"         // 响应 2xx
	KeyHTTP4xx         = "http_4xx"         // 响应 4xx
	KeyHTTP5xx         = "http_5xx"         // 响应 5xx（含网关自身 502/503）
	KeyRateLimited     = "ratelimit_reject" // 限流拒绝（429）
	KeyBreakerOpen     = "breaker_open"     // 熔断打开次数（状态迁移计数）
	KeyBreakerReject   = "breaker_reject"   // 熔断期间拒绝的请求数（503）
	KeyAllowlistReject = "allowlist_reject" // LLM 白名单拒绝（403）
	KeyTenantReject    = "tenant_reject"    // 租户校验拒绝（403）
	KeyContentReject   = "content_reject"   // 内容安全拒绝（403）
	KeyAuthReject      = "auth_reject"      // 鉴权失败（401/403）
)

var (
	mu     sync.RWMutex
	counts = map[string]int64{}
)

// Inc 计数器 +1（key 不在白名单时静默忽略，防止误传敏感维度）。
func Inc(key string) {
	Add(key, 1)
}

// Add 计数器 +n。
func Add(key string, n int64) {
	if !allowed(key) || n == 0 {
		return
	}
	mu.Lock()
	counts[key] += n
	mu.Unlock()
}

// Snapshot 返回当前计数快照（无序 map，由调用方序列化）。
func Snapshot() map[string]int64 {
	mu.RLock()
	defer mu.RUnlock()
	out := make(map[string]int64, len(counts))
	for k, v := range counts {
		out[k] = v
	}
	return out
}

// Keys 返回白名单键的有序列表（测试/文档用）。
func Keys() []string {
	keys := []string{
		KeyRequests, KeyHTTP2xx, KeyHTTP4xx, KeyHTTP5xx,
		KeyRateLimited, KeyBreakerOpen, KeyBreakerReject,
		KeyAllowlistReject, KeyTenantReject, KeyContentReject, KeyAuthReject,
	}
	sort.Strings(keys)
	return keys
}

// Reset 清空计数器（仅测试用）。
func Reset() {
	mu.Lock()
	counts = map[string]int64{}
	mu.Unlock()
}

func allowed(key string) bool {
	for _, k := range Keys() {
		if k == key {
			return true
		}
	}
	return false
}

// 编译期检查：白名单表与常量一一对应（漏加常量时编译失败）。
var _ = map[string]bool{
	KeyRequests: true, KeyHTTP2xx: true, KeyHTTP4xx: true, KeyHTTP5xx: true,
	KeyRateLimited: true, KeyBreakerOpen: true, KeyBreakerReject: true,
	KeyAllowlistReject: true, KeyTenantReject: true, KeyContentReject: true,
	KeyAuthReject: true,
}
