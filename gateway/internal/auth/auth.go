package auth

import (
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
	"gateway/internal/audit"
)

// Wrap 根据 mode 给下游 handler 加鉴权：
//   - "jwt_c" : C 端用户态 JWT 校验
//   - "rbac"  : B 端管理员校验，且要求 role 在白名单内
//   - ""      : 不鉴权（如静态资源、健康检查）
func Wrap(next http.Handler, mode, secret string, rbacRoles []string) http.Handler {
	if mode == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tok := extractBearer(r)
		if tok == "" {
			http.Error(w, `{"error":"missing token"}`, 401)
			return
		}
		claims, err := parseJWT(tok, secret)
		if err != nil {
			http.Error(w, `{"error":"invalid token"}`, 401)
			return
		}
		if mode == "rbac" {
			role, _ := claims["role"].(string)
			if !contains(rbacRoles, role) {
				http.Error(w, `{"error":"forbidden"}`, 403)
				return
			}
		}
		// 注入 X-User-* 身份头，供下游（如 Python/FastAPI 独立进程）识别调用者；
		// 使用 Header.Set 覆盖任何客户端传入的同名头，防止伪造。
		injectIdentityHeaders(r, claims)
		// 同步身份到审计元信息（供最外层审计中间件落盘 uid/role/tenant）
		if m := audit.MetaFromContext(r.Context()); m != nil {
			m.UserID = r.Header.Get("X-User-Id")
			m.Role = r.Header.Get("X-User-Role")
			m.TenantID = r.Header.Get("X-Tenant-Id")
		}
		// 同时保留 context 透传（同进程内下游 handler 可用）
		ctx := withClaims(r.Context(), claims)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func extractBearer(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	return ""
}

func parseJWT(tokenStr, secret string) (jwt.MapClaims, error) {
	tok, err := jwt.Parse(tokenStr, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, jwt.ErrSignatureInvalid
		}
		return []byte(secret), nil
	})
	if err != nil {
		return nil, err
	}
	claims, ok := tok.Claims.(jwt.MapClaims)
	if !ok || !tok.Valid {
		return nil, jwt.ErrTokenInvalidClaims
	}
	return claims, nil
}

func contains(s []string, v string) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

// injectIdentityHeaders 将 JWT 中的身份声明注入为 X-User-* 请求头，
// 供下游后端（独立进程，无法读取本进程 context）识别身份。
//
// 无条件写语义（安全关键）：每个 X-User-* 头都必须 Set——claim 存在则写真实值，
// 不存在则写空串。绝不能「claim 不存在就跳过 Set」，否则客户端自带的伪造头会
// 原样透传给下游，造成越权/跨租户（例如用有效学生 token 伪造 X-User-Role: admin、
// X-Tenant-Id: 别的学校，而 /api/app/ 走 jwt_c 不校验角色，请求会直达后端）。
func injectIdentityHeaders(r *http.Request, claims jwt.MapClaims) {
	// 取值：显式存在且非空才采用，malformed claim(类型不符)视同不存在
	userID := firstNonEmpty(claims, "sub", "uid")
	role := stringClaim(claims, "role")
	tenantID := stringClaim(claims, "tenant_id")

	// 无条件 Set：即使为空也要覆盖，清除客户端可能的伪造
	r.Header.Set("X-User-Id", userID)
	r.Header.Set("X-User-Role", role)
	r.Header.Set("X-Tenant-Id", tenantID)
}

// stringClaim 取 string 类型的 claim；不存在或类型不符返回空串。
func stringClaim(c jwt.MapClaims, key string) string {
	if v, ok := c[key].(string); ok {
		return v
	}
	return ""
}

// firstNonEmpty 按优先级取第一个非空取值。
func firstNonEmpty(c jwt.MapClaims, keys ...string) string {
	for _, k := range keys {
		if v := stringClaim(c, k); v != "" {
			return v
		}
	}
	return ""
}
