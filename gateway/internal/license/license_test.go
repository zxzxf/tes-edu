package license

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// TestBeatSuccess：心跳成功 → 状态 ok、lastSuccess 有值。
func TestBeatSuccess(t *testing.T) {
	var gotBody map[string]any
	var gotContentType string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotContentType = r.Header.Get("Content-Type")
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	h := New(Options{URL: srv.URL, InstanceID: "inst-123", Version: "v1.2.3"})
	h.beat()

	st := h.Status()
	if st.State != "ok" {
		t.Fatalf("状态应 ok，实际 %v", st.State)
	}
	if st.LastSuccess == nil {
		t.Fatal("lastSuccess 应有值")
	}
	if st.TotalSent != 1 || st.TotalFailed != 0 {
		t.Fatalf("计数不符: %+v", st)
	}
	if gotContentType != "application/json" {
		t.Fatalf("Content-Type 应为 application/json，实际 %q", gotContentType)
	}
	if gotBody["instance_id"] != "inst-123" || gotBody["version"] != "v1.2.3" {
		t.Fatalf("心跳体缺少匿名标识: %v", gotBody)
	}
	// 隐私：心跳体只允许这三个键
	if len(gotBody) != 3 {
		t.Fatalf("心跳体应只含 instance_id/version/ts，实际 %v", gotBody)
	}
}

// TestDegradedAfterFailures：连续失败达阈值 → degraded + 回调触发。
func TestDegradedAfterFailures(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	var transitions int32
	h := New(Options{
		URL:           srv.URL,
		DegradedAfter: 2,
		OnStateChange: func(from, to string) { atomic.AddInt32(&transitions, 1) },
	})

	h.beat() // 失败 1
	if s := h.Status(); s.State != "starting" {
		t.Fatalf("1 次失败不应 degraded，实际 %v", s.State)
	}
	h.beat() // 失败 2 → degraded
	st := h.Status()
	if st.State != "degraded" {
		t.Fatalf("2 次失败应 degraded，实际 %v", st.State)
	}
	if st.ConsecutiveFailure != 2 || st.TotalFailed != 2 {
		t.Fatalf("失败计数不符: %+v", st)
	}
	if atomic.LoadInt32(&transitions) != 1 {
		t.Fatalf("状态迁移回调应触发 1 次，实际 %d", transitions)
	}
}

// TestRecovery：degraded 后心跳恢复 → ok + 回调。
func TestRecovery(t *testing.T) {
	var fail atomic.Bool
	fail.Store(true)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if fail.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	var recovered int32
	h := New(Options{
		URL:           srv.URL,
		DegradedAfter: 1,
		OnStateChange: func(from, to string) {
			if to == "ok" {
				atomic.AddInt32(&recovered, 1)
			}
		},
	})
	h.beat() // 失败 → degraded
	if h.Status().State != "degraded" {
		t.Fatal("应 degraded")
	}
	fail.Store(false)
	h.beat() // 恢复
	if h.Status().State != "ok" {
		t.Fatalf("应恢复 ok，实际 %v", h.Status().State)
	}
	if atomic.LoadInt32(&recovered) != 1 {
		t.Fatal("恢复回调应触发")
	}
}

// TestUnreachableURL：URL 不可达不 panic，记失败。
func TestUnreachableURL(t *testing.T) {
	h := New(Options{URL: "http://127.0.0.1:1/nope", Timeout: 500 * time.Millisecond})
	h.beat()
	if st := h.Status(); st.TotalFailed != 1 || st.LastAttempt == nil {
		t.Fatalf("不可达应记失败: %+v", st)
	}
}

// TestStartStop：Start/Stop 生命周期干净退出。
func TestStartStop(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	h := New(Options{URL: srv.URL, Interval: 20 * time.Millisecond})
	ctx, cancel := context.WithCancel(context.Background())
	h.Start(ctx)
	time.Sleep(80 * time.Millisecond) // 覆盖启动即发 + 至少 1 个周期
	h.Stop()
	cancel()

	if atomic.LoadInt32(&hits) < 2 {
		t.Fatalf("启动即发 + 周期心跳应至少 2 次，实际 %d", hits)
	}
	// Stop 后不再发
	before := atomic.LoadInt32(&hits)
	time.Sleep(60 * time.Millisecond)
	if atomic.LoadInt32(&hits) != before {
		t.Fatal("Stop 后不应再发心跳")
	}
}
