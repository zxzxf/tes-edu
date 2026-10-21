// Package allowlist 实现 LLM 模型 endpoint 白名单校验（P5 合同硬要求之一）：
// "仅接受 OpenAI 兼容 /v1 接口，白名单外 endpoint 由网关拒绝"。
//
// 契约（agent-backend 侧）：
//   - 凡是要调用 LLM 的请求，agent-backend 必须在请求头（默认 X-LLM-Endpoint）
//     声明目标模型端点（完整 URL，如 https://api.deepseek.com/v1）；
//   - 网关对该头做白名单校验：命中放行，未命中 403 + 审计日志；
//   - 未带该头的请求视为非 LLM 请求，默认放行（require_header=true 时改为拒绝）。
//
// 匹配语义：按 URL 组件比对——scheme 与 host:port 必须完全一致，
// path 必须是白名单条目路径前缀的子路径（条目路径为空表示任意路径）。
// 例：白名单 "https://api.deepseek.com/v1" 命中
//
//	https://api.deepseek.com/v1/chat/completions ✅
//	https://api.deepseek.com/v2/chat/completions ❌（路径前缀不符）
//	http://api.deepseek.com/v1/...               ❌（scheme 不符）
//	https://api.deepseek.com:8443/v1/...         ❌（端口不符）
package allowlist

import (
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strings"
)

// Entry 白名单条目（规范化后的端点）。
type Entry struct {
	Scheme     string // 小写，http/https
	Host       string // 小写，含端口（默认端口已显式化）
	PathPrefix string // 小写，"" 表示任意路径
	Raw        string // 原始配置串（日志用）
}

// Options 白名单选项。
type Options struct {
	// Enabled 总开关。关闭时中间件直接放行（不校验）。
	Enabled bool
	// Header 声明目标端点的请求头名。
	Header string
	// RequireHeader true 时，缺失该头的请求也拒绝（fail-closed）；
	// false 时缺失头视为非 LLM 请求放行。
	RequireHeader bool
	// Entries 静态白名单条目（兼容既有用法：启动时一次性解析）。
	Entries []Entry
	// EntriesFn 动态条目源（configsync 热更新）。非 nil 时每请求调用获取
	// 当前生效条目，优先于 Entries；返回值须已规范化（经 ParseEndpoints）。
	EntriesFn func() []Entry
}

// ParseEndpoints 把配置串解析为规范化条目。非法 URL 直接报错——
// 白名单写错不应该静默变成"放行所有"。
func ParseEndpoints(raw []string) ([]Entry, error) {
	entries := make([]Entry, 0, len(raw))
	for _, s := range raw {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		u, err := url.Parse(s)
		if err != nil || u.Scheme == "" || u.Host == "" {
			return nil, fmt.Errorf("非法 endpoint %q：必须是完整 URL（如 https://api.deepseek.com/v1）", s)
		}
		e := Entry{
			Scheme:     strings.ToLower(u.Scheme),
			Host:       normalizeHost(u),
			PathPrefix: strings.ToLower(strings.TrimRight(u.Path, "/")),
			Raw:        s,
		}
		entries = append(entries, e)
	}
	return entries, nil
}

// normalizeHost 规范化 host：小写 + 补全默认端口，保证
// "api.deepseek.com" 与 "api.deepseek.com:443" 视为同一端点。
func normalizeHost(u *url.URL) string {
	host := strings.ToLower(u.Host)
	if !strings.Contains(host, ":") {
		switch strings.ToLower(u.Scheme) {
		case "https":
			host += ":443"
		case "http":
			host += ":80"
		}
	}
	return host
}

// Match 判断端点是否命中白名单。
func Match(entries []Entry, endpoint string) bool {
	u, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || u.Scheme == "" || u.Host == "" {
		return false // 畸形/相对 URL 一律不命中
	}
	scheme := strings.ToLower(u.Scheme)
	host := normalizeHost(u)
	path := strings.ToLower(strings.TrimRight(u.Path, "/"))
	for _, e := range entries {
		if e.Scheme != scheme || e.Host != host {
			continue
		}
		if e.PathPrefix == "" || path == e.PathPrefix || strings.HasPrefix(path, e.PathPrefix+"/") {
			return true
		}
	}
	return false
}

// Wrap 给 handler 加白名单校验。拒绝时返回 403 并打审计日志。
// 注意 fail-closed 语义：启用后若白名单为空，任何带端点头的请求都会被拒
// （P5 灌入合同锁定的 endpoint 之前，宁可拒绝也不放行）。
func Wrap(next http.Handler, opt Options) http.Handler {
	if !opt.Enabled {
		return next
	}
	header := opt.Header
	if header == "" {
		header = "X-LLM-Endpoint"
	}
	// 条目来源：EntriesFn（热更新，每请求取当前快照）优先；
	// 否则用静态 Entries（兼容既有用法与单测）。
	getEntries := opt.EntriesFn
	if getEntries == nil {
		static := opt.Entries
		getEntries = func() []Entry { return static }
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ep := strings.TrimSpace(r.Header.Get(header))
		if ep == "" {
			if opt.RequireHeader {
				log.Printf("[allowlist] reject: missing %s header, path=%s uid=%s",
					header, r.URL.Path, r.Header.Get("X-User-Id"))
				http.Error(w, `{"error":"llm endpoint header required"}`, http.StatusForbidden)
				return
			}
			next.ServeHTTP(w, r) // 非 LLM 请求，放行
			return
		}
		if !Match(getEntries(), ep) {
			log.Printf("[allowlist] reject: endpoint %q not in whitelist, path=%s uid=%s",
				ep, r.URL.Path, r.Header.Get("X-User-Id"))
			http.Error(w, `{"error":"llm endpoint not allowed"}`, http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}
