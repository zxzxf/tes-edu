package audit

import (
	"context"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Meta 在审计最外层创建，由内层鉴权中间件填充身份。
// 请求结束后由审计中间件读取并落盘。通过请求级 context 共享同一指针，
// 使外层审计能拿到内层鉴权后的用户/租户信息。
type Meta struct {
	UserID   string
	Role     string
	TenantID string
}

type ctxKey int

const metaKey ctxKey = 0

// MetaFromContext 供鉴权中间件读取并填充身份（共享指针）。
func MetaFromContext(ctx context.Context) *Meta {
	if m, ok := ctx.Value(metaKey).(*Meta); ok {
		return m
	}
	return nil
}

// Log 记录访问日志（审计落盘）。path 为空则仅输出到 stderr。
// 返回的 io.Closer 用于关闭底层日志文件句柄；进程退出（优雅退出流程）或
// 测试收尾时应调用 Close 释放文件句柄。path 为空时 Close 为 no-op。
func Log(next http.Handler, path string) (http.Handler, io.Closer) {
	var logger *log.Logger
	var closer io.Closer = nopCloser{}
	if path != "" {
		if f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
			logger = log.New(f, "", log.LstdFlags)
			closer = f
		}
	}
	if logger == nil {
		logger = log.Default()
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 创建请求级审计元信息，供内层鉴权填充身份
		meta := &Meta{}
		r = r.WithContext(context.WithValue(r.Context(), metaKey, meta))

		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)

		// 探活请求不写审计：/healthz 由 LB/编排层高频轮询，落入审计日志
		// 只会产生大量无意义噪声并浪费存储（验证见 TestLogSkipsHealthz）。
		if r.URL.Path == "/healthz" {
			return
		}

		// 请求处理完成后，元信息已由鉴权中间件填充，统一落盘（PII 脱敏后）
		logger.Printf("%s", MaskPII(fmt.Sprintf("%s %s %s %d uid=%s role=%s tenant=%s %s %s",
			ClientIP(r), r.Method, r.URL.Path, sw.status,
			meta.UserID, meta.Role, meta.TenantID,
			r.UserAgent(), time.Since(start))))
	}), closer
}

// ClientIP 取客户端真实来源 IP，按信任链逐级回退：
//  1. CF-Connecting-IP：Cloudflare 边缘写入的最终用户 IP（经 cloudflared
//     隧道透传）。仅当流量确实经过 Cloudflare 时可信；
//  2. X-Real-IP：入口反代（edu-lb）以 proxy_set_header 覆盖写入的来源 IP，
//     同名头被替换，客户端无法伪造；
//  3. RemoteAddr：直连场景对端地址（去端口）。
//
// 背景：请求经 lb 转发后网关的 RemoteAddr 恒为 lb 私网容器 IP，所有请求
// 记同一来源、审计无可追溯性；X-Real-IP 恢复来源区分。注意：若 137:80
// 对公网开放，客户端可绕过 Cloudflare 伪造 CF-Connecting-IP，届时需去掉
// 第一级（当前源站经 cloudflared 隧道暴露，不建议开放 80 入站）。
func ClientIP(r *http.Request) string {
	for _, h := range []string{"CF-Connecting-IP", "X-Real-IP"} {
		if v := strings.TrimSpace(r.Header.Get(h)); v != "" {
			return v
		}
	}
	if host, _, err := net.SplitHostPort(r.RemoteAddr); err == nil {
		return host
	}
	return r.RemoteAddr
}

// nopCloser 是空操作的 Close 实现（无日志文件时使用）。
type nopCloser struct{}

func (nopCloser) Close() error { return nil }

// CleanupOldLogs 按保留期清理历史审计日志（数据留存策略的网关侧落地）。
//
// 规则：
//   - 只清理与活跃日志同目录、同基名前缀（如 access.log*）的文件；
//   - 活跃日志文件本身与保留期内的文件绝不删除；
//   - retentionDays<=0 表示不启用保留期（不删任何文件）；
//   - 返回被删除的文件路径列表（运维对账用）。
func CleanupOldLogs(logPath string, retentionDays int) []string {
	if retentionDays <= 0 || logPath == "" {
		return nil
	}
	dir := filepath.Dir(logPath)
	prefix := filepath.Base(logPath)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}

	cutoff := time.Now().AddDate(0, 0, -retentionDays)
	var deleted []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if name == prefix || !strings.HasPrefix(name, prefix) {
			continue // 跳过活跃文件与无关文件
		}
		info, err := e.Info()
		if err != nil || !info.ModTime().Before(cutoff) {
			continue
		}
		full := filepath.Join(dir, name)
		if err := os.Remove(full); err == nil {
			deleted = append(deleted, full)
		}
	}
	return deleted
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}
