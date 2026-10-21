package compliance

import (
	"log"
	"net/http"
	"strings"
)

// 请求体大小上限（防 payload 滥用）。
//   - JSON/表单路由：4MB（解题请求题干+图片 base64 之外不会更大）
//   - multipart（拍照上传）：15MB（手机拍照图常见 3–8MB，4MB 会误杀）
const (
	maxBodyBytes      = 4 << 20  // 4MB
	maxMultipartBytes = 15 << 20 // 15MB
)

// 合规模板中间件（骨架版）。
//
// 当前实现：
//  1. 基础输入校验：按内容类型限制请求体大小，防止 payload 滥用；
//     Content-Length 与 chunked（无 Content-Length）两种场景都覆盖；
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
		// 1) 基础输入校验：按内容类型取上限
		limit := bodyLimit(r)
		if r.ContentLength > limit {
			http.Error(w, `{"error":"payload too large"}`, http.StatusRequestEntityTooLarge)
			return
		}
		// chunked 上传没有 Content-Length，用 MaxBytesReader 兜底：
		// 读取超限时返回错误，由代理层转为 502，请求不会完整打到上游。
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, limit)
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

// bodyLimit 按请求内容类型返回体积上限：
// multipart/form-data（拍照上传）放宽到 15MB，其余 4MB。
func bodyLimit(r *http.Request) int64 {
	if isMultipart(r) {
		return maxMultipartBytes
	}
	return maxBodyBytes
}

// isMultipart 判断是否为文件上传请求（Content-Type: multipart/form-data，带 boundary 参数）。
func isMultipart(r *http.Request) bool {
	ct := r.Header.Get("Content-Type")
	if !strings.HasPrefix(strings.ToLower(ct), "multipart/form-data") {
		return false
	}
	// 防止 "multipart/form-data" 无 boundary 的畸形头绕过类型判断后仍按大限额放行
	return strings.Contains(strings.ToLower(ct), "boundary=")
}

// isSensitiveOp 判断是否为 B 端写操作（需重点审计的敏感操作）。
func isSensitiveOp(r *http.Request) bool {
	const prefix = "/api/admin/"
	return len(r.URL.Path) >= len(prefix) && r.URL.Path[:len(prefix)] == prefix && r.Method != http.MethodGet
}
