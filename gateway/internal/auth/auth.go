package auth

import (
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
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
// 使用 Header.Set 覆盖客户端传入的同名头，避免伪造。
func injectIdentityHeaders(r *http.Request, claims jwt.MapClaims) {
	if v, ok := claims["sub"].(string); ok && v != "" {
		r.Header.Set("X-User-Id", v)
	} else if v, ok := claims["uid"].(string); ok && v != "" {
		r.Header.Set("X-User-Id", v)
	}
	if v, ok := claims["role"].(string); ok && v != "" {
		r.Header.Set("X-User-Role", v)
	}
	if v, ok := claims["tenant_id"].(string); ok && v != "" {
		r.Header.Set("X-Tenant-Id", v)
	}
}
