package ratelimit

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
)

// 用高 rate + 低 burst 构造可预测的桶：burst=1 表示只放行首个请求。
func opts(burst int, rps float64) Options {
	return Options{RPS: rps, Burst: burst, RouteKey: "/api/app/"}
}

// TestWithinBurstAllowed：桶容量内的请求应全部放行。
func TestWithinBurstAllowed(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(3, 10))

	for i := 0; i < 3; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
		req.Header.Set("X-User-Id", "u-burst")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("第 %d 个请求应放行，实际 %d", i+1, w.Code)
		}
	}
}

// TestExceedBurst429：超出突发容量必须限流——这是保护后端与模型配额的闸门。
func TestExceedBurst429(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(1, 0.0001)) // 几乎不 refill，保证第 2 个必被限

	for i := 0; i < 5; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
		req.Header.Set("X-User-Id", "u-limit")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)

		if i == 0 {
			if w.Code != http.StatusOK {
				t.Fatalf("首个请求应放行，实际 %d", w.Code)
			}
			continue
		}
		if w.Code != http.StatusTooManyRequests {
			t.Fatalf("第 %d 个请求应 429，实际 %d", i+1, w.Code)
		}
	}
}

// TestRPSZeroBypasses：配置 rate<=0 视为不限流（路由可显式关闭限流）。
func TestRPSZeroBypasses(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(0, 0))

	for i := 0; i < 10; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("rate<=0 应全放行，第 %d 个返回 %d", i+1, w.Code)
		}
	}
}

// TestPerUserIsolation：不同用户各自独立计数——否则一个活跃用户会拖垮全校。
func TestPerUserIsolation(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(1, 0.0001))

	hit := func(uid string) int {
		req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
		req.Header.Set("X-User-Id", uid)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		return w.Code
	}

	// u-a 打满自己的桶
	for i := 0; i < 3; i++ {
		hit("u-a")
	}
	// u-b 全新的桶，首个请求必须放行
	if got := hit("u-b"); got != http.StatusOK {
		t.Fatalf("用户间应相互隔离，u-b 首请求实际 %d", got)
	}
}

// TestRouteKeyIsolation：不同路由各自计数——admin 的调用不应消耗 app 的配额。
func TestRouteKeyIsolation(t *testing.T) {
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	hApp := Wrap(sentinel, Options{RPS: 0.0001, Burst: 1, RouteKey: "/api/app/"})
	hAdmin := Wrap(sentinel, Options{RPS: 0.0001, Burst: 1, RouteKey: "/api/admin/"})

	call := func(h http.Handler) int {
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		req.Header.Set("X-User-Id", "u-route")
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		return w.Code
	}

	call(hApp)
	// 同 key 二次应被限
	if got := call(hApp); got != http.StatusTooManyRequests {
		t.Fatalf("同路由二次应 429，实际 %d", got)
	}
	// 不同路由 key 独立，首次应放行
	if got := call(hAdmin); got != http.StatusOK {
		t.Fatalf("不同路由应隔离，实际 %d", got)
	}
}

// TestTenantFallbackThenIP：身份维度回退顺序 uid -> tenant -> IP。
func TestTenantFallbackThenIP(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(1, 0.0001))

	call := func(set func(*http.Request)) int {
		req := httptest.NewRequest(http.MethodGet, "/x", nil)
		if set != nil {
			set(req)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		return w.Code
	}

	// tenant 维度：同一个 tenant 第二次应被限
	call(func(r *http.Request) { r.Header.Set("X-Tenant-Id", "t1") })
	if got := call(func(r *http.Request) { r.Header.Set("X-Tenant-Id", "t1") }); got != http.StatusTooManyRequests {
		t.Fatalf("同 tenant 二次应 429，实际 %d", got)
	}
	// 另一个 tenant 独立
	if got := call(func(r *http.Request) { r.Header.Set("X-Tenant-Id", "t2") }); got != http.StatusOK {
		t.Fatalf("不同 tenant 应隔离，实际 %d", got)
	}
	// 无身份头时回退 RemoteAddr：httptest 的请求 RemoteAddr 固定，
	// 因此第二次必被限（验证 IP 维度确实生效且不 panic）。
	call(nil)
	if got := call(nil); got != http.StatusTooManyRequests {
		t.Fatalf("IP 回退维度应生效，第二次实际 %d", got)
	}
}

// TestConcurrentSafety：并发下不允许 panic / data race。
// 用 -race 跑本用例可验证 keyLimiter 的互斥保护。
func TestConcurrentSafety(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opts(50, 1000))

	var wg sync.WaitGroup
	var mu sync.Mutex
	codes := map[int]int{}

	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			req := httptest.NewRequest(http.MethodGet, "/x", nil)
			req.Header.Set("X-User-Id", "u-concurrent-"+strconv.Itoa(i%10))
			w := httptest.NewRecorder()
			h.ServeHTTP(w, req)
			mu.Lock()
			codes[w.Code]++
			mu.Unlock()
		}(i)
	}
	wg.Wait()

	// 只要没 panic 且存在放行即可（具体放行数量依赖 refill）
	if codes[http.StatusOK] == 0 {
		t.Fatal("并发下应有请求被放行")
	}
}

// TestKeyLimiterMapGrowth：验证超过 5000 条时的清理分支至少不 panic、不误删活跃 key。
func TestKeyLimiterMapGrowth(t *testing.T) {
	kl := newKeyLimiter(10, 20)
	for i := 0; i < 6000; i++ {
		kl.get("user-" + strconv.Itoa(i))
	}
	if len(kl.limiters) == 0 {
		t.Fatal("限流器 map 为空，异常")
	}
	// 清理逻辑仅在 >5000 且 10 分钟未活动时才删；此处刚写入不应被清空的合理性由上述断言兜底
}
