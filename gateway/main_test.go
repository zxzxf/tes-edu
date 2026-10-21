package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"gateway/internal/config"
	"gateway/internal/metrics"
)

// makeToken 用与网关一致的 HS256 算法签发测试 JWT。
func makeToken(t *testing.T, secret string, claims jwt.MapClaims) string {
	t.Helper()
	tok := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	s, err := tok.SignedString([]byte(secret))
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return s
}

// recordingServer 记录收到的 POST 次数，其余方法返回 200 + JSON。
func recordingServer() (*httptest.Server, *int32) {
	var posts int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		atomic.AddInt32(&posts, 1)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "path": r.URL.Path})
	}))
	return srv, &posts
}

func TestGatewayEndToEnd(t *testing.T) {
	metrics.Reset()

	upstream, upCount := recordingServer()
	defer upstream.Close()
	licSrv, licCount := recordingServer()
	defer licSrv.Close()
	telSrv, telCount := recordingServer()
	defer telSrv.Close()

	secret := "testsecret"
	cfg := &config.Config{
		Listen:             ":0",
		Upstreams:          map[string]string{"mock": upstream.URL},
		JWTSecret:          secret,
		AuditRetentionDays: 0,
		InstanceID:         "test-instance",
		Version:            "test-1",
		Redis:              config.RedisConfig{Enabled: false},
		License:            config.LicenseConfig{Enabled: true, URL: licSrv.URL, IntervalSeconds: 1},
		Telemetry:          config.TelemetryConfig{Enabled: true, URL: telSrv.URL, IntervalSeconds: 1},
		ContentSafety:      config.ContentSafetyConfig{Enabled: true, Keywords: []string{"forbiddenword"}, MaxScanBytes: 262144},
		Routes: []config.Route{
			{Match: "/api/app/", Upstream: "mock", Mode: "jwt_c", Rate: 1e6, Burst: 2e6},
			{Match: "/api/admin/", Upstream: "mock", Mode: "rbac", Roles: []string{"admin", "teacher"},
				RequireTenant: true, Rate: 1e6, Burst: 2e6},
			{Match: "/", Upstream: "mock", Mode: "", Rate: 1e6, Burst: 2e6},
		},
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	mux, _, cleanup := buildHandler(cfg, ctx)
	defer cleanup()

	gw := httptest.NewServer(mux)
	defer gw.Close()

	cTok := makeToken(t, secret, jwt.MapClaims{"sub": "u123", "role": "student", "tenant_id": "t1"})
	adminNoTenant := makeToken(t, secret, jwt.MapClaims{"sub": "adm1", "role": "admin"})
	adminTenant := makeToken(t, secret, jwt.MapClaims{"sub": "adm1", "role": "admin", "tenant_id": "schoolA"})

	do := func(method, path, token string, body string) (int, string) {
		req, _ := http.NewRequest(method, gw.URL+path, strings.NewReader(body))
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		if body != "" {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", method, path, err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(b)
	}

	// 1) C 端正常请求 -> 200，且真正打到上游
	if s, _ := do("GET", "/api/app/hello", cTok, ""); s != 200 {
		t.Fatalf("c-endpoint expected 200, got %d", s)
	}
	if atomic.LoadInt32(upCount) < 1 {
		t.Fatalf("upstream not reached for c-endpoint")
	}

	// 2) admin 无租户 -> 403（多租户强制隔离）
	if s, _ := do("GET", "/api/admin/x", adminNoTenant, ""); s != 403 {
		t.Fatalf("admin-no-tenant expected 403, got %d", s)
	}

	// 3) admin 有租户 -> 200
	if s, _ := do("GET", "/api/admin/x", adminTenant, ""); s != 200 {
		t.Fatalf("admin-tenant expected 200, got %d", s)
	}

	// 4) 无 token 访问受保护路由 -> 401
	if s, _ := do("GET", "/api/app/hello", "", ""); s != 401 {
		t.Fatalf("no-token expected 401, got %d", s)
	}

	// 5) 内容安全：敏感词 -> 403
	if s, _ := do("POST", "/api/app/chat", cTok, `{"text":"this contains forbiddenword"}`); s != 403 {
		t.Fatalf("content-reject expected 403, got %d", s)
	}

	// 6) 内容安全：正常文本 -> 200（body 还原，打到上游）
	if s, _ := do("POST", "/api/app/chat", cTok, `{"text":"hello safe"}`); s != 200 {
		t.Fatalf("content-pass expected 200, got %d", s)
	}

	// 7) licensez 仅本机可访问（httptest 为 loopback）-> 200 含 state
	if s, b := do("GET", "/licensez", "", ""); s != 200 || !strings.Contains(b, "state") {
		t.Fatalf("licensez expected 200+state, got %d %s", s, b)
	}

	// 8) metricsz -> 200 含 counters + instance_id
	if s, b := do("GET", "/metricsz", "", ""); s != 200 || !strings.Contains(b, "counters") {
		t.Fatalf("metricsz expected 200+counters, got %d %s", s, b)
	}

	// 等待心跳/指标上报（interval=1s，启动即发一次）
	time.Sleep(2500 * time.Millisecond)
	if atomic.LoadInt32(licCount) < 1 {
		t.Fatalf("license heartbeat not received by receiver (posts=%d)", atomic.LoadInt32(licCount))
	}
	if atomic.LoadInt32(telCount) < 1 {
		t.Fatalf("telemetry not received by receiver (posts=%d)", atomic.LoadInt32(telCount))
	}

	// 9) 匿名指标白名单计数器已累加（不依赖网络）
	snap := metrics.Snapshot()
	if snap[metrics.KeyContentReject] < 1 {
		t.Fatalf("content_reject metric not incremented: %v", snap)
	}
	if snap[metrics.KeyTenantReject] < 1 {
		t.Fatalf("tenant_reject metric not incremented: %v", snap)
	}
	if snap[metrics.KeyAuthReject] < 1 {
		t.Fatalf("auth_reject metric not incremented: %v", snap)
	}
}
