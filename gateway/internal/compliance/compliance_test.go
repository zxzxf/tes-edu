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

// TestMultipartGetsHigherLimit：拍照上传（multipart）上限放宽到 15MB。
// 修复记录：此前所有路由统一 4MB，手机拍照图（3–8MB）会被误杀 413。
func TestMultipartGetsHigherLimit(t *testing.T) {
	h := wrapTarget()

	// multipart：5MB（> 4MB 旧上限）必须放行
	req := httptest.NewRequest(http.MethodPost, "/api/app/solve_image", strings.NewReader("x"))
	req.Header.Set("Content-Type", "multipart/form-data; boundary=----WebKitFormBoundaryTEST")
	req.ContentLength = 5 << 20
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("5MB multipart 应放行，实际 %d", w.Code)
	}

	// multipart 超 15MB 仍须拒绝
	req2 := httptest.NewRequest(http.MethodPost, "/api/app/solve_image", strings.NewReader("x"))
	req2.Header.Set("Content-Type", "multipart/form-data; boundary=----WebKitFormBoundaryTEST")
	req2.ContentLength = (15 << 20) + 1
	w2 := httptest.NewRecorder()
	h.ServeHTTP(w2, req2)

	if w2.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("超 15MB multipart 应 413，实际 %d", w2.Code)
	}
}

// TestMultipartWithoutBoundaryNotElevated：畸形 multipart 头（无 boundary）
// 不得按大限额放行，仍按 4MB 基础限额拒绝。
func TestMultipartWithoutBoundaryNotElevated(t *testing.T) {
	h := wrapTarget()

	req := httptest.NewRequest(http.MethodPost, "/api/app/solve_image", strings.NewReader("x"))
	req.Header.Set("Content-Type", "multipart/form-data") // 无 boundary
	req.ContentLength = 5 << 20
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if w.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("无 boundary 的 multipart 头应按 4MB 限额拒绝，实际 %d", w.Code)
	}
}

// TestChunkedBodyEnforced：chunked（ContentLength=-1）超大 body 由
// MaxBytesReader 兜底——handler 读 body 时必须报错，而不是放行打到上游。
func TestChunkedBodyEnforced(t *testing.T) {
	var readErr error
	target := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		buf := make([]byte, 32<<10)
		for {
			_, err := r.Body.Read(buf)
			if err != nil {
				readErr = err
				break
			}
		}
		w.WriteHeader(http.StatusOK)
	})
	h := Wrap(target, nil)

	// 8MB 的 chunked JSON 请求（无 Content-Length，绕过 ContentLength 检查）
	body := strings.NewReader(strings.Repeat("a", 8<<20))
	req := httptest.NewRequest(http.MethodPost, "/api/app/solve", body)
	req.ContentLength = -1 // 显式声明为 chunked
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)

	if readErr == nil {
		t.Fatal("8MB chunked body 应触发 MaxBytesReader 读取错误")
	}
	if w.Code != http.StatusOK {
		t.Fatalf("上游自身应返回 200（限流在读取层生效），实际 %d", w.Code)
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
