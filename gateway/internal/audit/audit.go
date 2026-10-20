package audit

import (
	"log"
	"net/http"
	"os"
	"time"
)

// Log 记录访问日志（审计落盘）。path 为空则仅输出到 stderr。
func Log(next http.Handler, path string) http.Handler {
	var logger *log.Logger
	if path != "" {
		if f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
			logger = log.New(f, "", log.LstdFlags)
		}
	}
	if logger == nil {
		logger = log.Default()
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)
		logger.Printf("%s %s %s %d %s %s",
			r.RemoteAddr, r.Method, r.URL.Path, sw.status, r.UserAgent(), time.Since(start))
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (s *statusWriter) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}
