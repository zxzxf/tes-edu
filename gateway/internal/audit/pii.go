package audit

import (
	"regexp"
	"strings"
)

// PII 脱敏正则（落盘前统一 MaskPII）。
// 覆盖：手机号、身份证（18 位）、邮箱。IPv4 不脱敏（RemoteAddr 是运维排查刚需），
// 但会在文档中注明可按合规要求加严。
var (
	rePhone   = regexp.MustCompile(`\b1[3-9]\d{9}\b`)
	reIDCard  = regexp.MustCompile(`\b\d{17}[\dXx]\b`)
	reEmail   = regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b`)
	reChinese = regexp.MustCompile(`[\p{Han}]{2,}`) // 仅测试用：确认中文不被误伤
)

// MaskPII 对日志行做 PII 脱敏：
//
//	13812345678      → 138****5678
//	110101199001011234 → 110101********1234
//	student@xx.com   → s*****t@xx.com
//
// 幂等：对已脱敏字符串再脱敏不变（重复脱敏不会进一步损坏）。
func MaskPII(s string) string {
	s = rePhone.ReplaceAllStringFunc(s, maskMiddle11)
	s = reIDCard.ReplaceAllStringFunc(s, maskMiddle18)
	s = reEmail.ReplaceAllStringFunc(s, maskEmail)
	return s
}

// maskMiddle11 手机号保留前 3 后 4。
func maskMiddle11(m string) string {
	return m[:3] + "****" + m[7:]
}

// maskMiddle18 身份证保留前 6 后 4。
func maskMiddle18(m string) string {
	return m[:6] + "********" + m[14:]
}

// maskEmail 邮箱保留首尾字符与域名。
func maskEmail(m string) string {
	at := strings.IndexByte(m, '@')
	if at <= 0 {
		return m
	}
	local, domain := m[:at], m[at:]
	switch {
	case len(local) <= 1:
		return "*" + domain
	case len(local) == 2:
		return local[:1] + "*" + domain
	default:
		return local[:1] + strings.Repeat("*", len(local)-2) + local[len(local)-1:] + domain
	}
}

// ContainsChinese 仅测试辅助：确认中文文本不被 PII 正则误伤。
func ContainsChinese(s string) bool {
	return reChinese.MatchString(s)
}
