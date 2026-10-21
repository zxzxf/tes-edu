package compliance

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func wrapTarget() http.Handler {
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("OK"))
	})
	return Wrap(sentinel, nil)
}

// TestRejectsOversizedBody：声明 Content-Length 超过 4MB 必须 413。
// 拍照上传是主要入口（图片 multipart），这条限制是 payload 滥用的第一道闸。
func TestRejectsOversizedBody(t *testing.T) {
	h := wrapTarget()

	req := httptest.NewRequest(http.MethodPost, "/api/app/solve_image", strings.NewReader("x"))
	req.ContentLength = (4 << 20) + 1 // 4MB + 1
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("超大 body 应 413，实际 %d", w.Code)
	}
}

// TestAllowsNormalBody：正常体积放行，不能误伤。
func TestAllowsNormalBody(t *testing.T) {
	h := wrapTarget()

	req := httptest.NewRequest(http.MethodPost, "/api/app/solve", strings.NewReader(`{"q":"1+1"}`))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("正常 body 应 200，实际 %d", w.Code)
	}
}

// TestBoundaryAtLimit：恰好等于 4MB 边界应放行（> 才拒绝）。
func TestBoundaryAtLimit(t *testing.T) {
	h := wrapTarget()

	req := httptest.NewRequest(http.MethodPost, "/api/app/solve_image", strings.NewReader("x"))
	req.ContentLength = 4 << 20
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("等于上限应放行，实际 %d", w.Code)
	}
}

// TestIsSensitiveOp：仅 B 端写操作计入敏感审计；C 端与 GET 不计。
// 这条决定审计噪声大小——把所有 GET 都记下来会让日志失控。
func TestIsSensitiveOp(t *testing.T) {
	cases := []struct {
		path   string
		method string
		want   bool
	}{
		{"/api/admin/knowledge", http.MethodPost, true},
		{"/api/admin/exam", http.MethodDelete, true},
		{"/api/admin/users", http.MethodPut, true},
		{"/api/admin/knowledge", http.MethodGet, false}, // GET 不敏感
		{"/api/app/solve", http.MethodPost, false},      // C 端不计
		{"/healthz", http.MethodPost, false},
	}
	for _, c := range cases {
		req := httptest.NewRequest(c.method, c.path, nil)
		if got := isSensitiveOp(req); got != c.want {
			t.Errorf("isSensitiveOp(%s %s) = %v, want %v", c.method, c.path, got, c.want)
		}
	}
}

// TestEdgePathNotPanic：短路径/边界路径不应触发切片越界。
// 注意：httptest.NewRequest 不接受空 target，故此处改用 http.NewRequest 构造。
func TestEdgePathNotPanic(t *testing.T) {
	for _, p := range []string{"", "/", "/a", "/api", "/api/admin"} {
		req, err := http.NewRequest(http.MethodPost, "http://example.com"+p, nil)
		if err != nil {
			t.Fatalf("构造请求 %q 失败: %v", p, err)
		}
		req.URL.Path = p // 直接置为待测路径，绕过 URL 解析规范化
		_ = isSensitiveOp(req) // 只要求不 panic（prefix 切片越界是最典型的坑）
	}
}
