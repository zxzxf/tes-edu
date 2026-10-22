package audit

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// TestLogWritesToFile：审计是合规的落地点，必须真正写盘。
func TestLogWritesToFile(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
	}), logPath)
	defer closer.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.RemoteAddr = "10.0.0.9:1234"
	h.ServeHTTP(httptest.NewRecorder(), req)

	b, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("审计日志文件未生成: %v", err)
	}
	s := string(b)
	for _, want := range []string{"/api/app/x", "10.0.0.9", "201"} {
		if !contains(s, want) {
			t.Errorf("审计日志缺少 %q，实际内容: %q", want, s)
		}
	}
}

// TestLogSkipsHealthz：探活请求 /healthz 不应写入审计日志（治理审计噪声）。
func TestLogSkipsHealthz(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), logPath)
	defer closer.Close()

	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	h.ServeHTTP(httptest.NewRecorder(), req)

	// 审计直接跳过，文件可能不生成；若生成则不得含 /healthz
	b, err := os.ReadFile(logPath)
	if err == nil && contains(string(b), "/healthz") {
		t.Errorf("探活请求不应写入审计日志，实际: %q", string(b))
	}
}

// TestLogRecordsActualStatus：必须记录后端真实状态码（201/404/500），而非恒记 200。
// statusWriter 的 WriteHeader 覆盖是这里的关键实现。
func TestLogRecordsActualStatus(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}), logPath)
	defer closer.Close()

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))

	b, _ := os.ReadFile(logPath)
	if !contains(string(b), "500") {
		t.Errorf("应记录真实状态码 500，实际: %q", string(b))
	}
}

// TestImplicitWriteHeadStatus：handler 只写 body 不显式调 WriteHeader 时，默认应为 200。
func TestImplicitWriteHeadStatus(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("ok")) // 隐式 200
	}), logPath)
	defer closer.Close()

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))

	b, _ := os.ReadFile(logPath)
	if !contains(string(b), "200") {
		t.Errorf("隐式 WriteHeader 应记录 200，实际: %q", string(b))
	}
}

// TestEmptyPathFallsBackToStderr：未配置文件路径时不应崩溃（回退到标准 logger）。
func TestEmptyPathFallsBackToStderr(t *testing.T) {
	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), "")
	defer closer.Close()

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))
	// 能跑到这里即通过
}

// TestMetaFromContext：Meta 指针在 context 中共享，供内层中间件回填身份。
// 外层建、内层填、外层读——这是审计能带上 uid/role/tenant 的机制。
func TestMetaFromContext(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 模拟鉴权中间件：取到外层 Meta 指针并回填身份
		if m := MetaFromContext(r.Context()); m != nil {
			m.UserID = "u99"
			m.Role = "admin"
			m.TenantID = "t5"
		}
		w.WriteHeader(http.StatusOK)
	}), logPath)
	defer closer.Close()

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/x", nil))

	b, _ := os.ReadFile(logPath)
	s := string(b)
	for _, want := range []string{"uid=u99", "role=admin", "tenant=t5"} {
		if !contains(s, want) {
			t.Errorf("审计日志应含 %q，实际: %q", want, s)
		}
	}
}

// TestMetaAbsentOutsideLog：不在 Log 包裹下的 context 没有 Meta，应返回 nil（避免 nil 解引用）。
func TestMetaAbsentOutsideLog(t *testing.T) {
	if m := MetaFromContext(context.Background()); m != nil {
		t.Fatalf("无 Meta 的 context 应返回 nil，实际 %+v", m)
	}
}

// TestClientIP：审计来源 IP 按信任链取值（CF-Connecting-IP > X-Real-IP > RemoteAddr）。
func TestClientIP(t *testing.T) {
	newReq := func() *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/x", nil)
		r.RemoteAddr = "172.18.0.5:34567"
		return r
	}

	r := newReq()
	r.Header.Set("X-Real-IP", "203.0.113.7")
	if got := clientIP(r); got != "203.0.113.7" {
		t.Errorf("X-Real-IP 应优先于 RemoteAddr，got %q", got)
	}

	r = newReq()
	r.Header.Set("CF-Connecting-IP", "198.51.100.9")
	r.Header.Set("X-Real-IP", "172.18.0.9")
	if got := clientIP(r); got != "198.51.100.9" {
		t.Errorf("CF-Connecting-IP 应最优先，got %q", got)
	}

	r = newReq()
	if got := clientIP(r); got != "172.18.0.5" {
		t.Errorf("无头应回退 RemoteAddr 并去端口，got %q", got)
	}

	r.RemoteAddr = "172.18.0.5" // 无端口不坏
	if got := clientIP(r); got != "172.18.0.5" {
		t.Errorf("无端口 RemoteAddr 应原样返回，got %q", got)
	}
}

// TestLogPrefersXRealIP：经 lb 转发的请求，审计必须记 X-Real-IP 而非 lb 私网 IP
// （RemoteAddr 恒为 lb 容器地址，直接记录等于所有请求同一来源）。
func TestLogPrefersXRealIP(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "access.log")

	h, closer := Log(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), logPath)
	defer closer.Close()

	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.RemoteAddr = "172.18.0.5:34567"       // lb 容器 IP
	req.Header.Set("X-Real-IP", "203.0.113.7") // lb 覆盖写入的真实来源
	h.ServeHTTP(httptest.NewRecorder(), req)

	b, _ := os.ReadFile(logPath)
	s := string(b)
	if !contains(s, "203.0.113.7") {
		t.Errorf("审计应记录 X-Real-IP 203.0.113.7，实际: %q", s)
	}
	if contains(s, "172.18.0.5") {
		t.Errorf("审计不应记录 lb 私网 IP，实际: %q", s)
	}
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
