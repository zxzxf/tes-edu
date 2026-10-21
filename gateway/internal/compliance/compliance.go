// Package compliance 合规模块（骨架升级版）。
//
// 三层能力：
//  1. 输入校验：按内容类型限制请求体大小（JSON 4MB / multipart 15MB），
//     chunked 无 Content-Length 时由 MaxBytesReader 兜底；
//  2. 内容安全（未成年人保护）：对 JSON/文本请求体做敏感词扫描，
//     命中即 403 + 审计日志。multipart（二进制图片）不扫描——
//     大体重放与逐字节审计由后端/对象存储侧负责，网关只做大小闸门；
//  3. 敏感操作审计：B 端写操作日志（PII 脱敏后落盘）。
//
// 后续可在此接入统一内容安全服务（替代本地词表），接口已按中间件形态留好。
package compliance

import (
	"bytes"
	"io"
	"log"
	"net/http"
	"strings"

	"gateway/internal/audit"
	"gateway/internal/metrics"
)

// 请求体大小上限（防 payload 滥用）。
//   - JSON/表单路由：4MB（解题请求题干+图片 base64 之外不会更大）
//   - multipart（拍照上传）：15MB（手机拍照图常见 3–8MB，4MB 会误杀）
const (
	maxBodyBytes      = 4 << 20  // 4MB
	maxMultipartBytes = 15 << 20 // 15MB
)

// Options 合规选项。
type Options struct {
	// Logger 合规日志（nil 用 log.Default()）。
	Logger *log.Logger
	// ContentSafetyEnabled 敏感词扫描总开关。
	ContentSafetyEnabled bool
	// Keywords 静态敏感词表（兼容既有用法：小写比较，命中即拒绝）。
	Keywords []string
	// KeywordsFn 动态敏感词源（configsync 热更新）。非 nil 时每请求调用获取
	// 当前词表，优先于 Keywords；返回值须已规范化为小写。
	KeywordsFn func() []string
	// MaxScanBytes 单请求体扫描上限（默认 256KB；超出不扫描直接放行，
	// 由后端深审——网关不做大体重放）。
	MaxScanBytes int64
}

// Wrap 构造合规中间件。
func Wrap(next http.Handler, opt Options) http.Handler {
	logger := opt.Logger
	if logger == nil {
		logger = log.Default()
	}
	// 词表来源：KeywordsFn（configsync 热更，已规范化小写）优先；
	// 否则用静态 Keywords（启动时规范化一次，兼容既有用法/单测）。
	var getKeywords func() []string
	if opt.KeywordsFn != nil {
		getKeywords = opt.KeywordsFn
	} else {
		norm := make([]string, 0, len(opt.Keywords))
		seen := make(map[string]struct{}, len(opt.Keywords))
		for _, k := range opt.Keywords {
			k = strings.ToLower(strings.TrimSpace(k))
			if k == "" {
				continue
			}
			if _, dup := seen[k]; dup {
				continue
			}
			seen[k] = struct{}{}
			norm = append(norm, k)
		}
		getKeywords = func() []string { return norm }
	}
	maxScan := opt.MaxScanBytes
	if maxScan <= 0 {
		maxScan = 256 << 10 // 256KB
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

		// 2) 内容安全：JSON/文本请求体敏感词扫描
		if opt.ContentSafetyEnabled && scannable(r) && r.ContentLength > 0 && r.ContentLength <= maxScan {
			body, err := io.ReadAll(r.Body)
			if err != nil {
				http.Error(w, `{"error":"payload too large"}`, http.StatusRequestEntityTooLarge)
				return
			}
			if kw := scanKeywords(string(body), getKeywords()); kw != "" {
				metrics.Inc(metrics.KeyContentReject)
				logger.Printf("[compliance] content_reject keyword=%q path=%s method=%s uid=%s",
					audit.MaskPII(kw), r.URL.Path, r.Method, r.Header.Get("X-User-Id"))
				http.Error(w, `{"error":"content rejected"}`, http.StatusForbidden)
				return
			}
			// 未命中：还原 body 供下游读取（扫描已消费原 reader）
			r.Body = io.NopCloser(bytes.NewReader(body))
		}

		// 3) 敏感操作审计（骨架：仅日志，后续接入审核/脱敏服务）
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

// scannable 判断请求体是否可做文本扫描：JSON/文本且非 multipart。
func scannable(r *http.Request) bool {
	if isMultipart(r) {
		return false
	}
	switch r.Method {
	case http.MethodPost, http.MethodPut, http.MethodPatch:
	default:
		return false
	}
	ct := strings.ToLower(r.Header.Get("Content-Type"))
	return strings.HasPrefix(ct, "application/json") || strings.HasPrefix(ct, "text/")
}

// scanKeywords 返回第一个命中的敏感词（小写比较）；未命中返回 ""。
func scanKeywords(body string, keywords []string) string {
	lower := strings.ToLower(body)
	for _, kw := range keywords {
		if kw != "" && strings.Contains(lower, kw) {
			return kw
		}
	}
	return ""
}

// isSensitiveOp 判断是否为 B 端写操作（需重点审计的敏感操作）。
func isSensitiveOp(r *http.Request) bool {
	const prefix = "/api/admin/"
	return len(r.URL.Path) >= len(prefix) && r.URL.Path[:len(prefix)] == prefix && r.Method != http.MethodGet
}
