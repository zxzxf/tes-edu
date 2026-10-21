package telemetry

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"gateway/internal/metrics"
)

// TestReportSuccess：回传体只含匿名字段 + 白名单计数器。
func TestReportSuccess(t *testing.T) {
	metrics.Reset()
	metrics.Inc(metrics.KeyRequests)
	metrics.Inc(metrics.KeyHTTP5xx)

	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r := New(Options{URL: srv.URL, InstanceID: "inst-1", Version: "v1"})
	r.report()

	counters, ok := got["counters"].(map[string]any)
	if !ok {
		t.Fatalf("缺少 counters: %v", got)
	}
	if counters["requests"] != float64(1) || counters["http_5xx"] != float64(1) {
		t.Fatalf("计数器不符: %v", counters)
	}
	// 隐私红线：顶层只允许这四个键
	if len(got) != 4 {
		t.Fatalf("回传体应只含 instance_id/version/ts/counters，实际 %v", got)
	}
}

// TestReportFailureSilent：回传失败不 panic、不返回 error（纯旁路）。
func TestReportFailureSilent(t *testing.T) {
	metrics.Reset()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	r := New(Options{URL: srv.URL, Timeout: time.Second})
	r.report() // 只要求不 panic
}

// TestReportUnreachable：端点不可达不 panic。
func TestReportUnreachable(t *testing.T) {
	r := New(Options{URL: "http://127.0.0.1:1/nope", Timeout: 500 * time.Millisecond})
	r.report()
}

// TestStartStop：生命周期干净退出。
func TestStartStop(t *testing.T) {
	hits := make(chan struct{}, 10)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits <- struct{}{}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r := New(Options{URL: srv.URL, Interval: 20 * time.Millisecond})
	ctx, cancel := context.WithCancel(context.Background())
	r.Start(ctx)
	select {
	case <-hits: // 启动即报
	case <-time.After(2 * time.Second):
		t.Fatal("启动后应至少回传一次")
	}
	select {
	case <-hits: // 周期回传
	case <-time.After(2 * time.Second):
		t.Fatal("应按周期回传")
	}
	cancel()
	time.Sleep(60 * time.Millisecond)
	// cancel 后允许在途一次完成，之后应停止：再等一个周期验证无新上报
	select {
	case <-hits:
		t.Fatal("cancel 后不应继续回传")
	case <-time.After(100 * time.Millisecond):
	}
}
