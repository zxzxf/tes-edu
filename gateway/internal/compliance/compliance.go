package compliance

import (
	"log"
	"net/http"
)

// 合规模板中间件（骨架版）。
//
// 当前实现：
//  1. 基础输入校验：拒绝超大请求体，防止 payload 滥用；
//  2. 敏感操作审计：对 B 端写操作（/api/admin/ 下非 GET）做合规日志记录。
//
// 后续应在此接入真实内容安全能力：
//   - 未成年人内容审核（请求体/响应体涉敏拦截）；
//   - PII 脱敏（手机号/身份证等敏感字段落盘前脱敏）；
//   - 数据留存策略（按合规要求控制日志保留周期）。
// 这些能力建议对接统一的内容安全服务，而非在各后端重复实现。
func Wrap(next http.Handler, logger *log.Logger) http.Handler {
	if logger == nil {
		logger = log.Default()
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 1) 基础输入校验
		const maxBytes = 4 << 20 // 4MB
		if r.ContentLength > maxBytes {
			http.Error(w, `{"error":"payload too large"}`, 413)
			return
		}

		// 2) 敏感操作审计（骨架：仅日志，后续接入审核/脱敏服务）
		if isSensitiveOp(r) {
			logger.Printf("[compliance] sensitive_op path=%s method=%s uid=%s role=%s",
				r.URL.Path, r.Method,
				r.Header.Get("X-User-Id"), r.Header.Get("X-User-Role"))
		}

		next.ServeHTTP(w, r)
	})
}

// isSensitiveOp 判断是否为 B 端写操作（需重点审计的敏感操作）。
func isSensitiveOp(r *http.Request) bool {
	const prefix = "/api/admin/"
	return len(r.URL.Path) >= len(prefix) && r.URL.Path[:len(prefix)] == prefix && r.Method != http.MethodGet
}
