package audit

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestMaskPII_Phone：手机号脱敏为 138****5678。
func TestMaskPII_Phone(t *testing.T) {
	got := MaskPII("学生联系电话 13812345678 已记录")
	want := "138****5678"
	if !strings.Contains(got, want) {
		t.Fatalf("手机号脱敏失败: %q", got)
	}
	if strings.Contains(got, "13812345678") {
		t.Fatal("原始手机号仍存在")
	}
}

// TestMaskPII_IDCard：身份证脱敏保留前 6 后 4。
func TestMaskPII_IDCard(t *testing.T) {
	got := MaskPII("证件号 110101199001011234")
	if !strings.Contains(got, "110101********1234") {
		t.Fatalf("身份证脱敏失败: %q", got)
	}
}

// TestMaskPII_Email：邮箱本地部分打码、域名保留。
func TestMaskPII_Email(t *testing.T) {
	got := MaskPII("联系 student@example.com")
	if !strings.Contains(got, "s*****t@example.com") {
		t.Fatalf("邮箱脱敏失败: %q", got)
	}
	if strings.Contains(got, "student@example.com") {
		t.Fatal("原始邮箱仍存在")
	}
}

// TestMaskPII_Idempotent：二次脱敏不进一步损坏。
func TestMaskPII_Idempotent(t *testing.T) {
	once := MaskPII("13812345678 110101199001011234 a@b.com")
	twice := MaskPII(once)
	if once != twice {
		t.Fatalf("脱敏不幂等:\n once=%q\ntwice=%q", once, twice)
	}
}

// TestMaskPII_KeepsChinese：中文与普通文本不被误伤。
func TestMaskPII_KeepsChinese(t *testing.T) {
	in := "2026/10/21 22:00 127.0.0.1 GET /api/app/solve 200 uid=u42"
	got := MaskPII(in)
	if !ContainsChinese("保留中文") {
		t.Fatal("中文检测辅助函数失效")
	}
	if got != in {
		t.Fatalf("无 PII 的行不应被改动:\n in=%q\ngot=%q", in, got)
	}
}

// TestMaskPII_MultipleInLine：一行多个 PII 全部脱敏。
func TestMaskPII_MultipleInLine(t *testing.T) {
	got := MaskPII("a 13812345678 b 13998765432 c")
	if strings.Contains(got, "13812345678") || strings.Contains(got, "13998765432") {
		t.Fatalf("多个手机号应全部脱敏: %q", got)
	}
}

// TestCleanupOldLogs：过期日志删除、活跃日志与近期日志保留。
func TestCleanupOldLogs(t *testing.T) {
	dir := t.TempDir()
	active := filepath.Join(dir, "access.log")
	old := filepath.Join(dir, "access.log.2026-09-01")
	recent := filepath.Join(dir, "access.log.2026-10-20")
	unrelated := filepath.Join(dir, "other.log")

	now := time.Now()
	write := func(p string, age time.Duration) {
		if err := os.WriteFile(p, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
		m := now.Add(-age)
		_ = os.Chtimes(p, m, m)
	}
	write(active, 0)
	write(old, 40*24*time.Hour)
	write(recent, 2*24*time.Hour)
	write(unrelated, 40*24*time.Hour)

	deleted := CleanupOldLogs(active, 30)
	if len(deleted) != 1 || filepath.Base(deleted[0]) != "access.log.2026-09-01" {
		t.Fatalf("应只删除 40 天前的同前缀文件，实际 %v", deleted)
	}
	// 活跃文件、近期文件、无关文件都必须在
	for _, p := range []string{active, recent, unrelated} {
		if _, err := os.Stat(p); err != nil {
			t.Fatalf("%s 不应被删除: %v", p, err)
		}
	}
}

// TestCleanupOldLogs_Disabled：retention<=0 时不删任何文件。
func TestCleanupOldLogs_Disabled(t *testing.T) {
	dir := t.TempDir()
	old := filepath.Join(dir, "access.log.2020-01-01")
	if err := os.WriteFile(old, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	m := time.Now().Add(-1000 * 24 * time.Hour)
	_ = os.Chtimes(old, m, m)

	if deleted := CleanupOldLogs(filepath.Join(dir, "access.log"), 0); len(deleted) != 0 {
		t.Fatalf("retention<=0 不应删除，实际 %v", deleted)
	}
}
