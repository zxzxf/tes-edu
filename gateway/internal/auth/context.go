package auth

import (
	"context"

	"github.com/golang-jwt/jwt/v5"
)

type ctxKey int

const claimsKey ctxKey = 0

func withClaims(ctx context.Context, c jwt.MapClaims) context.Context {
	return context.WithValue(ctx, claimsKey, c)
}

// ClaimsFromContext 供下游 handler 取用（可透传给后端）
func ClaimsFromContext(ctx context.Context) (jwt.MapClaims, bool) {
	c, ok := ctx.Value(claimsKey).(jwt.MapClaims)
	return c, ok
}
