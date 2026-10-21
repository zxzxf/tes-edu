// Package breaker 实现按上游（upstream）维度的三态熔断器：
//
//	Closed  —— 正常放行；连续失败达到阈值后切 Open
//	Open    —— 直接拒绝（503 + Retry-After），不再打上游，保护僵死后端
//	HalfOpen—— 冷却结束后放行单个探测请求：成功→Closed，失败→重新 Open
//
// 设计取舍：
//   - 按 upstream 而非按路由：多个路由指向同一后端时共享熔断态，
//     避免 /api/app/ 把 agent-backend 打挂后 /api/admin/ 还在继续锤。
//   - 失败判定 = 上游返回 5xx 或代理层错误（连不上/超时），
//     由 breaker.Wrap 在响应结束后回填。
//   - 状态变更打日志：运维需要知道"什么时候开始熔断、什么时候恢复"。
package breaker

import (
	"log"
	"net/http"
	"sync"
	"time"

	"gateway/internal/metrics"
)

// State 熔断器状态。
type State int

const (
	// StateClosed 闭合：请求正常放行。
	StateClosed State = iota
	// StateOpen 打开：请求直接拒绝，不触达上游。
	StateOpen
	// StateHalfOpen 半开：冷却结束，放行受限探测请求。
	StateHalfOpen
)

func (s State) String() string {
	switch s {
	case StateClosed:
		return "closed"
	case StateOpen:
		return "open"
	case StateHalfOpen:
		return "half-open"
	default:
		return "unknown"
	}
}

// Options 熔断参数。
type Options struct {
	// FailureThreshold 连续失败多少次后打开熔断（>=1）。
	FailureThreshold int
	// Cooldown Open 持续多久后进入 HalfOpen 尝试恢复。
	Cooldown time.Duration
	// Name 上游标识，仅用于日志。
	Name string
}

// Breaker 单上游熔断器，并发安全。
type Breaker struct {
	opt Options

	mu            sync.Mutex
	state         State
	failures      int       // Closed 态下的连续失败计数
	openedAt      time.Time // 进入 Open 的时刻
	halfOpenProbe bool      // HalfOpen 态是否已有探测请求在飞
}

// New 构造熔断器。threshold<=0 或 cooldown<=0 时使用默认值（5 次 / 10 秒）。
func New(opt Options) *Breaker {
	if opt.FailureThreshold <= 0 {
		opt.FailureThreshold = 5
	}
	if opt.Cooldown <= 0 {
		opt.Cooldown = 10 * time.Second
	}
	return &Breaker{opt: opt, state: StateClosed}
}

// Allow 判断请求是否放行。
// 返回 (allowed, state)：Open 态直接拒绝；HalfOpen 态仅放行一个探测请求。
func (b *Breaker) Allow() (bool, State) {
	b.mu.Lock()
	defer b.mu.Unlock()

	switch b.state {
	case StateOpen:
		// 冷却期满，切半开，放行本请求作为探测
		if time.Since(b.openedAt) >= b.opt.Cooldown {
			b.setState(StateHalfOpen)
			b.halfOpenProbe = true
			return true, StateHalfOpen
		}
		return false, StateOpen
	case StateHalfOpen:
		// 半开态已有探测在飞，其余请求继续拒绝（避免探测期流量涌入）
		if b.halfOpenProbe {
			return false, StateHalfOpen
		}
		b.halfOpenProbe = true
		return true, StateHalfOpen
	default: // StateClosed
		return true, StateClosed
	}
}

// Success 记录一次成功：HalfOpen 探测成功→闭合；Closed 态清零失败计数。
func (b *Breaker) Success() {
	b.mu.Lock()
	defer b.mu.Unlock()

	switch b.state {
	case StateHalfOpen:
		b.setState(StateClosed) // 探测成功，恢复正常
		b.failures = 0
		b.halfOpenProbe = false
	default:
		b.failures = 0
	}
}

// Failure 记录一次失败：Closed 累计到阈值→打开；HalfOpen 探测失败→立即重新打开。
func (b *Breaker) Failure() {
	b.mu.Lock()
	defer b.mu.Unlock()

	switch b.state {
	case StateHalfOpen:
		// 探测失败：后端未恢复，重新打开并重置冷却
		b.setState(StateOpen)
		b.openedAt = time.Now()
		b.halfOpenProbe = false
	case StateClosed:
		b.failures++
		if b.failures >= b.opt.FailureThreshold {
			b.setState(StateOpen)
			b.openedAt = time.Now()
		}
	}
}

// State 返回当前状态（观测用）。
func (b *Breaker) State() State {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.state
}

// setState 状态迁移并打日志（调用方需持有锁）。
func (b *Breaker) setState(to State) {
	from := b.state
	b.state = to
	log.Printf("breaker[%s]: %s -> %s", b.opt.Name, from, to)
	// 打开熔断计入匿名指标（telemetry 回传的 KeyBreakerOpen 维度）
	if to == StateOpen {
		metrics.Inc(metrics.KeyBreakerOpen)
	}
}

// Wrap 给 handler 加熔断保护：
//   - 熔断打开时直接返回 503 + Retry-After，不打上游；
//   - 响应状态码 >= failureStatus（默认 500）或代理错误记为失败，否则记成功。
//
// 位置：中间件链最靠近代理的一层（鉴权/限流/合规之内），
// 保证熔断统计的是真实上游结果，且不浪费鉴权/限流开销。
func Wrap(next http.Handler, b *Breaker, failureStatus int) http.Handler {
	if failureStatus <= 0 {
		failureStatus = http.StatusInternalServerError
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		allowed, _ := b.Allow()
		if !allowed {
			metrics.Inc(metrics.KeyBreakerReject)
			w.Header().Set("Retry-After", retryAfter(b))
			http.Error(w, `{"error":"upstream circuit open"}`, http.StatusServiceUnavailable)
			return
		}

		sw := &statusWriter{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(sw, r)

		if sw.status >= failureStatus {
			b.Failure()
		} else {
			b.Success()
		}
	})
}

// retryAfter 返回建议客户端多少秒后重试（= 剩余冷却时间，向上取整）。
func retryAfter(b *Breaker) string {
	// 直接从 Options 读冷却值：Open 态下剩余时间最多就是 Cooldown，
	// 给一个保守的整数值即可，避免额外加锁读 openedAt。
	secs := int(b.opt.Cooldown/time.Second) + 1
	if secs < 1 {
		secs = 1
	}
	return itoa(secs)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[i:])
}

// statusWriter 捕获真实响应状态码（httptest/代理错误也会走到 WriteHeader）。
type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}
