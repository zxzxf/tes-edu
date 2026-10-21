package allowlist

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func testEntries(t *testing.T, raw ...string) []Entry {
	t.Helper()
	e, err := ParseEndpoints(raw)
	if err != nil {
		t.Fatalf("ParseEndpoints(%v): %v", raw, err)
	}
	return e
}

// TestMatchExactAndPrefix：命中与路径前缀语义。
func TestMatchExactAndPrefix(t *testing.T) {
	entries := testEntries(t, "https://api.deepseek.com/v1", "http://model.internal:8000")

	cases := []struct {
		endpoint string
		want     bool
	}{
		{"https://api.deepseek.com/v1/chat/completions", true},
		{"https://api.deepseek.com/v1", true},
		{"https://api.deepseek.com/v1/models", true},
		{"https://api.deepseek.com/v2/chat/completions", false},  // 路径前缀不符
		{"https://api.deepseek.com/chat/completions", false},     // 少一层 /v1
		{"http://api.deepseek.com/v1/chat/completions", false},   // scheme 不符
		{"https://api.deepseek.com:8443/v1/x", false},            // 端口不符
		{"https://evil.com/v1", false},                           // 完全不符
		{"http://model.internal:8000/v1/chat/completions", true}, // host-only 条目：任意路径
		{"http://model.internal:8000", true},
		{"http://model.internal:8001/v1", false},   // 端口不符
		{"https://API.DEEPSEEK.COM/V1/chat", true}, // 大小写不敏感
		{"not-a-url", false},                       // 畸形
		{"", false},                                // 空
	}
	for _, c := range cases {
		if got := Match(entries, c.endpoint); got != c.want {
			t.Errorf("Match(%q) = %v, want %v", c.endpoint, got, c.want)
		}
	}
}

// TestDefaultPortNormalization：省略端口与显式默认端口视为同一端点。
func TestDefaultPortNormalization(t *testing.T) {
	entries := testEntries(t, "https://api.deepseek.com/v1") // 无端口
	if !Match(entries, "https://api.deepseek.com:443/v1/chat") {
		t.Error("省略端口与 :443 应视为同一端点")
	}
}

// TestParseEndpointsRejectsGarbage：非法配置必须报错，不能静默放行。
func TestParseEndpointsRejectsGarbage(t *testing.T) {
	for _, bad := range []string{"api.deepseek.com", "/v1/relative", "://missing-scheme", "http://"} {
		if _, err := ParseEndpoints([]string{bad}); err == nil {
			t.Errorf("ParseEndpoints(%q) 应报错", bad)
		}
	}
}

func wrapTest(t *testing.T, opt Options) http.Handler {
	t.Helper()
	return Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), opt)
}

func doReq(h http.Handler, endpoint string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/api/app/solve", nil)
	if endpoint != "" {
		req.Header.Set("X-LLM-Endpoint", endpoint)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

// TestWrapRejectsNonWhitelisted：白名单外 endpoint 必须 403 且不打上游。
func TestWrapRejectsNonWhitelisted(t *testing.T) {
	h := wrapTest(t, Options{
		Enabled: true,
		Entries: testEntries(t, "https://api.deepseek.com/v1"),
	})

	w := doReq(h, "https://evil.example.com/v1")
	if w.Code != http.StatusForbidden {
		t.Fatalf("白名单外 endpoint 应 403，实际 %d", w.Code)
	}
}

// TestWrapAllowsWhitelisted：白名单内放行。
func TestWrapAllowsWhitelisted(t *testing.T) {
	h := wrapTest(t, Options{
		Enabled: true,
		Entries: testEntries(t, "https://api.deepseek.com/v1"),
	})

	w := doReq(h, "https://api.deepseek.com/v1/chat/completions")
	if w.Code != http.StatusOK {
		t.Fatalf("白名单内应放行，实际 %d", w.Code)
	}
}

// TestWrapMissingHeaderPassesByDefault：无头请求默认放行（非 LLM 流量不受影响）。
func TestWrapMissingHeaderPassesByDefault(t *testing.T) {
	h := wrapTest(t, Options{
		Enabled: true,
		Entries: testEntries(t, "https://api.deepseek.com/v1"),
	})

	w := doReq(h, "")
	if w.Code != http.StatusOK {
		t.Fatalf("无头请求应放行，实际 %d", w.Code)
	}
}

// TestWrapRequireHeaderRejectsMissing：require_header=true 时无头即拒。
func TestWrapRequireHeaderRejectsMissing(t *testing.T) {
	h := wrapTest(t, Options{
		Enabled:       true,
		RequireHeader: true,
		Entries:       testEntries(t, "https://api.deepseek.com/v1"),
	})

	w := doReq(h, "")
	if w.Code != http.StatusForbidden {
		t.Fatalf("require_header 下无头应 403，实际 %d", w.Code)
	}
}

// TestWrapDisabledPassesEverything：关闭时完全放行（含白名单外）。
func TestWrapDisabledPassesEverything(t *testing.T) {
	h := wrapTest(t, Options{
		Enabled: false,
		Entries: testEntries(t, "https://api.deepseek.com/v1"),
	})

	w := doReq(h, "https://evil.example.com/v1")
	if w.Code != http.StatusOK {
		t.Fatalf("关闭时应放行，实际 %d", w.Code)
	}
}

// TestWrapFailClosedOnEmptyList：启用但白名单为空 → 带端点头的请求全拒
// （P5 灌入合同 endpoint 之前的 fail-closed 语义）。
func TestWrapFailClosedOnEmptyList(t *testing.T) {
	h := wrapTest(t, Options{Enabled: true})

	w := doReq(h, "https://api.deepseek.com/v1")
	if w.Code != http.StatusForbidden {
		t.Fatalf("空白名单应 fail-closed 403，实际 %d", w.Code)
	}
	// 无头请求仍放行
	if w2 := doReq(h, ""); w2.Code != http.StatusOK {
		t.Fatalf("无头请求仍应放行，实际 %d", w2.Code)
	}
}

// TestWrapCustomHeader：头名可配置。
func TestWrapCustomHeader(t *testing.T) {
	h := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}), Options{
		Enabled: true,
		Header:  "X-Model-Endpoint",
		Entries: testEntries(t, "https://api.deepseek.com/v1"),
	})

	req := httptest.NewRequest(http.MethodPost, "/x", nil)
	req.Header.Set("X-Model-Endpoint", "https://evil.com")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Fatalf("自定义头名应生效，实际 %d", w.Code)
	}
}
