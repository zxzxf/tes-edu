package audit

import (
	"context"
	"io"
	"log"
	"net/http"
	"os"
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

		// 请求处理完成后，元信息已由鉴权中间件填充，统一落盘
		logger.Printf("%s %s %s %d uid=%s role=%s tenant=%s %s %s",
			r.RemoteAddr, r.Method, r.URL.Path, sw.status,
			meta.UserID, meta.Role, meta.TenantID,
			r.UserAgent(), time.Since(start))
	}), closer
}

// nopCloser 是空操作的 Close 实现（无日志文件时使用）。
type nopCloser struct{}

func (nopCloser) Close() error { return nil }

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}
