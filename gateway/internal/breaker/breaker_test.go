package breaker

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func newTestBreaker(threshold int, cooldown time.Duration) *Breaker {
	return New(Options{Name: "test", FailureThreshold: threshold, Cooldown: cooldown})
}

// TestClosedToOpenAfterThreshold：连续失败达到阈值必须打开熔断。
func TestClosedToOpenAfterThreshold(t *testing.T) {
	b := newTestBreaker(3, 50*time.Millisecond)

	for i := 0; i < 2; i++ {
		b.Failure()
		if allowed, state := b.Allow(); !allowed || state != StateClosed {
			t.Fatalf("第 %d 次失败后应仍闭合，got allowed=%v state=%v", i+1, allowed, state)
		}
	}
	b.Failure() // 第 3 次，达到阈值
	allowed, state := b.Allow()
	if allowed || state != StateOpen {
		t.Fatalf("达到阈值后应打开，got allowed=%v state=%v", allowed, state)
	}
}

// TestSuccessResetsFailures：Closed 态一次成功清零失败计数（抖动不误熔断）。
func TestSuccessResetsFailures(t *testing.T) {
	b := newTestBreaker(3, time.Second)

	b.Failure()
	b.Failure()
	b.Success() // 清零
	b.Failure()
	b.Failure()
	if allowed, _ := b.Allow(); !allowed {
		t.Fatal("成功后计数已清零，两次失败不应打开熔断")
	}
}

// TestOpenToHalfOpenAfterCooldown：冷却期满放行单个探测请求。
func TestOpenToHalfOpenAfterCooldown(t *testing.T) {
	b := newTestBreaker(1, 30*time.Millisecond)

	b.Failure() // 立即打开
	if allowed, _ := b.Allow(); allowed {
		t.Fatal("刚打开应拒绝")
	}

	time.Sleep(40 * time.Millisecond) // 等冷却
	allowed, state := b.Allow()
	if !allowed || state != StateHalfOpen {
		t.Fatalf("冷却后应放行探测，got allowed=%v state=%v", allowed, state)
	}

	// 半开态第二个请求必须继续拒绝（探测期只放一个）
	allowed2, state2 := b.Allow()
	if allowed2 || state2 != StateHalfOpen {
		t.Fatalf("半开期仅放一个探测，got allowed=%v state=%v", allowed2, state2)
	}
}

// TestHalfOpenProbeSuccessCloses：探测成功→恢复闭合。
func TestHalfOpenProbeSuccessCloses(t *testing.T) {
	b := newTestBreaker(1, 20*time.Millisecond)

	b.Failure() // 打开
	time.Sleep(30 * time.Millisecond)
	if allowed, _ := b.Allow(); !allowed {
		t.Fatal("冷却后应放行探测")
	}
	b.Success() // 探测成功
	if b.State() != StateClosed {
		t.Fatalf("探测成功后应闭合，got %v", b.State())
	}
}

// TestHalfOpenProbeFailureReopens：探测失败→立即重新打开并重置冷却。
func TestHalfOpenProbeFailureReopens(t *testing.T) {
	b := newTestBreaker(1, 20*time.Millisecond)

	b.Failure() // 打开
	time.Sleep(30 * time.Millisecond)
	b.Allow() // 进入半开并消耗探测名额
	b.Failure()
	if b.State() != StateOpen {
		t.Fatalf("探测失败应重新打开，got %v", b.State())
	}
	// 重新打开后应立即拒绝（冷却已重置）
	if allowed, _ := b.Allow(); allowed {
		t.Fatal("重开后冷却重置，应立即拒绝")
	}
}

// TestWrapReturns503WhenOpen：熔断打开时 Wrap 直接 503，不触达上游。
func TestWrapReturns503WhenOpen(t *testing.T) {
	b := newTestBreaker(1, time.Second)
	b.Failure() // 打开

	upstreamHits := 0
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamHits++
		w.WriteHeader(http.StatusOK)
	}), b, 500)

	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/x", nil))

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("熔断打开应 503，实际 %d", w.Code)
	}
	if upstreamHits != 0 {
		t.Fatalf("熔断打开不应触达上游，实际hit %d 次", upstreamHits)
	}
	if w.Header().Get("Retry-After") == "" {
		t.Fatal("503 应带 Retry-After 头")
	}
}

// TestWrapCounts5xxAsFailure：上游 500 记失败，达到阈值后熔断。
func TestWrapCounts5xxAsFailure(t *testing.T) {
	b := newTestBreaker(2, time.Second)
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}), b, 500)

	for i := 0; i < 2; i++ {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))
	}
	if b.State() != StateOpen {
		t.Fatalf("两次 5xx 后应打开熔断，got %v", b.State())
	}
}

// TestWrapCounts502AsFailure：代理层错误（ErrorHandler 写的 502）也记失败。
func TestWrapCounts502AsFailure(t *testing.T) {
	b := newTestBreaker(1, time.Second)
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
	}), b, 500)

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))
	if b.State() != StateOpen {
		t.Fatalf("502 应记失败并打开，got %v", b.State())
	}
}

// TestWrapSuccessStaysClosed：2xx/4xx 不记失败（4xx 是客户端问题，不是上游故障）。
func TestWrapSuccessStaysClosed(t *testing.T) {
	b := newTestBreaker(2, time.Second)
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest) // 4xx：客户端错误
	}), b, 500)

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))
	if b.State() != StateClosed {
		t.Fatalf("4xx 不应打开熔断，got %v", b.State())
	}
}

// TestConcurrentAllow：高并发下 Allow/Success/Failure 不 panic、不数据竞争
// （跑 -race 才有意义，这里至少保证功能正确）。
func TestConcurrentAllow(t *testing.T) {
	b := newTestBreaker(5, 10*time.Millisecond)

	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			for j := 0; j < 20; j++ {
				allowed, _ := b.Allow()
				if allowed {
					if n%2 == 0 {
						b.Success()
					} else {
						b.Failure()
					}
				}
			}
		}(i)
	}
	wg.Wait()
	// 不校验最终状态（并发下不确定），只要求不 panic / 状态合法
	switch b.State() {
	case StateClosed, StateOpen, StateHalfOpen:
	default:
		t.Fatalf("非法状态: %v", b.State())
	}
}

// TestDefaultOptions：非法参数回退默认值（5 次 / 10s），不会除零或永不开。
func TestDefaultOptions(t *testing.T) {
	b := New(Options{Name: "d"})
	for i := 0; i < 5; i++ {
		b.Failure()
	}
	if b.State() != StateOpen {
		t.Fatalf("默认阈值 5 次后应打开，got %v", b.State())
	}
}
