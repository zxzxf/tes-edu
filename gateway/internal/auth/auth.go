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
		// 把解析出的 subject/role 透传给后端（可选）
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
