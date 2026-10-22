package auth

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// 测试用 RSA 密钥对：私钥模拟 web-backend 签发，公钥交给网关（auth 包）验证。
var (
	testPriv   *rsa.PrivateKey
	testPubPEM string
)

func init() {
	var err error
	testPriv, err = rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		panic(err)
	}
	pubBytes, err := x509.MarshalPKIXPublicKey(&testPriv.PublicKey)
	if err != nil {
		panic(err)
	}
	testPubPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubBytes}))
}

// 构造被测 handler：Wrap 之后接一个恒返回 200 的哨兵。
func wrapTarget(mode string, roles []string) http.Handler {
	return wrapTargetOpt(Options{Mode: mode, Roles: roles, PublicKey: testPubPEM})
}

func wrapTargetOpt(opt Options) http.Handler {
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("OK"))
	})
	return Wrap(sentinel, opt)
}

// sign 用测试私钥签发 RS256 token（与网关 parseJWT 约定一致）。
func sign(t *testing.T, claims jwt.MapClaims) string {
	t.Helper()
	if claims["exp"] == nil {
		claims["exp"] = time.Now().Add(time.Hour).Unix()
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	s, err := tok.SignedString(testPriv)
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	return s
}

func do(t *testing.T, h http.Handler, path, bearer string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodGet, path, nil)
	if bearer != "" {
		r.Header.Set("Authorization", "Bearer "+bearer)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

// --- jwt_c 模式：C 端用户态 ---

func TestJWTC_MissingToken401(t *testing.T) {
	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", "").Code; got != http.StatusUnauthorized {
		t.Fatalf("无 token 应 401，实际 %d", got)
	}
}

func TestJWTC_InvalidToken401(t *testing.T) {
	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", "garbage.token.here").Code; got != http.StatusUnauthorized {
		t.Fatalf("坏 token 应 401，实际 %d", got)
	}
}

// TestJWTC_WrongKey401：用别的私钥签的 token 不能通过——防跨密钥伪造（RS256 核心收益）。
func TestJWTC_WrongKey401(t *testing.T) {
	otherPriv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("gen key: %v", err)
	}
	foreign := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"sub": "u1", "exp": time.Now().Add(time.Hour).Unix()})
	s, _ := foreign.SignedString(otherPriv)

	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", s).Code; got != http.StatusUnauthorized {
		t.Fatalf("异密钥 token 应 401，实际 %d", got)
	}
}

// TestJWTC_WrongAlg401：HS256 等对称算法签的 token 必须被拒（网关仅接受 RS256）。
func TestJWTC_WrongAlg401(t *testing.T) {
	foreign := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{"sub": "u1", "exp": time.Now().Add(time.Hour).Unix()})
	s, _ := foreign.SignedString([]byte("some-secret"))

	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", s).Code; got != http.StatusUnauthorized {
		t.Fatalf("HS256 token 应 401（网关仅接受 RS256），实际 %d", got)
	}
}

// TestJWTC_ExpiredToken401：过期 token 必须拒绝（parseJWT 依赖 jwt/v5 的 exp 校验）。
func TestJWTC_ExpiredToken401(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "u1", "exp": time.Now().Add(-time.Minute).Unix()})
	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", tok).Code; got != http.StatusUnauthorized {
		t.Fatalf("过期 token 应 401，实际 %d", got)
	}
}

func TestJWTC_ValidToken200(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "u1"})
	h := wrapTarget("jwt_c", nil)
	if got := do(t, h, "/api/app/solve", tok).Code; got != http.StatusOK {
		t.Fatalf("有效 token 应 200，实际 %d", got)
	}
}

// --- rbac 模式：B 端角色白名单 ---

func TestRBAC_AllowedRole200(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "admin1", "role": "admin"})
	h := wrapTarget("rbac", []string{"admin", "teacher"})
	if got := do(t, h, "/api/admin/ping", tok).Code; got != http.StatusOK {
		t.Fatalf("admin 角色应 200，实际 %d", got)
	}
}

// TestRBAC_ForbiddenRole403：这是 B 端数据边界的最后一道闸。
func TestRBAC_ForbiddenRole403(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "stu1", "role": "student"})
	h := wrapTarget("rbac", []string{"admin", "teacher"})
	if got := do(t, h, "/api/admin/users", tok).Code; got != http.StatusForbidden {
		t.Fatalf("student 访问 admin 应 403，实际 %d", got)
	}
}

// TestRBAC_MissingRoleClaim403：没有 role 声明的 token 不能蒙混过关。
func TestRBAC_MissingRoleClaim403(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "someone"})
	h := wrapTarget("rbac", []string{"admin"})
	if got := do(t, h, "/api/admin/users", tok).Code; got != http.StatusForbidden {
		t.Fatalf("无 role 声明应 403，实际 %d", got)
	}
}

// --- 身份头注入与伪造防护 ---

// TestIdentityHeadersInjected：下游 Python 后端靠 X-User-* 识别调用者，必须注入。
func TestIdentityHeadersInjected(t *testing.T) {
	var got http.Header
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
	})
	h := Wrap(sentinel, Options{Mode: "jwt_c", PublicKey: testPubPEM})
	tok := sign(t, jwt.MapClaims{"sub": "u42", "role": "teacher", "tenant_id": "t7"})

	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	h.ServeHTTP(httptest.NewRecorder(), req)

	if got.Get("X-User-Id") != "u42" {
		t.Errorf("X-User-Id = %q, want u42", got.Get("X-User-Id"))
	}
	if got.Get("X-User-Role") != "teacher" {
		t.Errorf("X-User-Role = %q, want teacher", got.Get("X-User-Role"))
	}
	if got.Get("X-Tenant-Id") != "t7" {
		t.Errorf("X-Tenant-Id = %q, want t7", got.Get("X-Tenant-Id"))
	}
}

// TestIdentityHeadersOverwriteForged：客户端自带 X-User-Id 必须被覆盖，否则可冒充任意用户。
func TestIdentityHeadersOverwriteForged(t *testing.T) {
	var got http.Header
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
	})
	h := Wrap(sentinel, Options{Mode: "jwt_c", PublicKey: testPubPEM})
	tok := sign(t, jwt.MapClaims{"sub": "real-user"})

	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("X-User-Id", "attacker")       // 伪造
	req.Header.Set("X-User-Role", "admin")        // 伪造提权
	req.Header.Set("X-Tenant-Id", "other-tenant") // 伪造跨租户
	h.ServeHTTP(httptest.NewRecorder(), req)

	if got.Get("X-User-Id") != "real-user" {
		t.Errorf("伪造未被覆盖: X-User-Id = %q, want real-user", got.Get("X-User-Id"))
	}
	if got.Get("X-User-Role") != "" {
		t.Errorf("伪造的 role 应被清除为无 role，实际 %q", got.Get("X-User-Role"))
	}
	if got.Get("X-Tenant-Id") != "" {
		t.Errorf("伪造的 tenant 应被清除，实际 %q", got.Get("X-Tenant-Id"))
	}
}

// TestUIDFallback：没有 sub 但有 uid 的 token 也应注入（兼容两种 claim 命名）。
func TestUIDFallback(t *testing.T) {
	var got http.Header
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
	})
	h := Wrap(sentinel, Options{Mode: "jwt_c", PublicKey: testPubPEM})
	tok := sign(t, jwt.MapClaims{"uid": "legacy-user"})

	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	h.ServeHTTP(httptest.NewRecorder(), req)

	if got.Get("X-User-Id") != "legacy-user" {
		t.Errorf("uid 回退失败: %q", got.Get("X-User-Id"))
	}
}

// --- 空 mode：静态资源/健康检查旁路 ---

func TestEmptyModeBypass(t *testing.T) {
	h := wrapTarget("", nil)
	if got := do(t, h, "/anything", "").Code; got != http.StatusOK {
		t.Fatalf("mode=\"\" 应直接放行，实际 %d", got)
	}
}

// TestBearerPrefixExact："Bearer" 必须带空格，避免 "BearerXxx" 被误认。
func TestBearerPrefixExact(t *testing.T) {
	tok := sign(t, jwt.MapClaims{"sub": "u1"})
	h := wrapTarget("jwt_c", nil)
	// 不带空格，extractBearer 应返回空 -> 401
	req := httptest.NewRequest(http.MethodGet, "/api/app/x", nil)
	req.Header.Set("Authorization", "Bearerx"+tok)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("畸形 Authorization 应 401，实际 %d", w.Code)
	}
	if strings.TrimSpace(w.Body.String()) == "" {
		t.Error("401 应带 JSON 错误体")
	}
}

// --- 多租户强制隔离（require_tenant）---

func TestRequireTenant_MissingClaim403(t *testing.T) {
	h := wrapTargetOpt(Options{Mode: "jwt_c", PublicKey: testPubPEM, RequireTenant: true})
	tok := sign(t, jwt.MapClaims{"sub": "u1"}) // 无 tenant_id

	if got := do(t, h, "/api/admin/x", tok).Code; got != http.StatusForbidden {
		t.Fatalf("无租户声明应 403，实际 %d", got)
	}
}

func TestRequireTenant_WithClaim200(t *testing.T) {
	var got http.Header
	sentinel := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
	})
	h := Wrap(sentinel, Options{Mode: "jwt_c", PublicKey: testPubPEM, RequireTenant: true})
	tok := sign(t, jwt.MapClaims{"sub": "u1", "tenant_id": "school-42"})

	if code := do(t, h, "/api/admin/x", tok).Code; code != http.StatusOK {
		t.Fatalf("带租户声明应 200，实际 %d", code)
	}
	if got.Get("X-Tenant-Id") != "school-42" {
		t.Fatalf("X-Tenant-Id 注入失败: %q", got.Get("X-Tenant-Id"))
	}
}

func TestRequireTenant_EmptyClaim403(t *testing.T) {
	h := wrapTargetOpt(Options{Mode: "jwt_c", PublicKey: testPubPEM, RequireTenant: true})
	tok := sign(t, jwt.MapClaims{"sub": "u1", "tenant_id": ""})

	if got := do(t, h, "/api/admin/x", tok).Code; got != http.StatusForbidden {
		t.Fatalf("空 tenant_id 应 403，实际 %d", got)
	}
}

func TestRequireTenant_DisabledByDefault(t *testing.T) {
	h := wrapTargetOpt(Options{Mode: "jwt_c", PublicKey: testPubPEM})
	tok := sign(t, jwt.MapClaims{"sub": "u1"})

	if got := do(t, h, "/api/app/solve", tok).Code; got != http.StatusOK {
		t.Fatalf("未开启 require_tenant 应放行，实际 %d", got)
	}
}

func TestRequireTenant_RBACStillWorks(t *testing.T) {
	h := wrapTargetOpt(Options{
		Mode: "rbac", PublicKey: testPubPEM,
		Roles: []string{"admin"}, RequireTenant: true,
	})

	// 角色不符 → 403（角色优先）
	badRole := sign(t, jwt.MapClaims{"sub": "s", "role": "student", "tenant_id": "t1"})
	if got := do(t, h, "/api/admin/x", badRole).Code; got != http.StatusForbidden {
		t.Fatalf("角色不符应 403，实际 %d", got)
	}
	// 角色对但无租户 → 403
	noTenant := sign(t, jwt.MapClaims{"sub": "a", "role": "admin"})
	if got := do(t, h, "/api/admin/x", noTenant).Code; got != http.StatusForbidden {
		t.Fatalf("无租户应 403，实际 %d", got)
	}
	// 两者都对 → 200
	ok := sign(t, jwt.MapClaims{"sub": "a", "role": "admin", "tenant_id": "t1"})
	if got := do(t, h, "/api/admin/x", ok).Code; got != http.StatusOK {
		t.Fatalf("角色+租户齐全应 200，实际 %d", got)
	}
}
