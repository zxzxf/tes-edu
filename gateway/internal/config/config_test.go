package config

import (
	"os"
	"path/filepath"
	"testing"
)

// 用最小合法配置起手，避免每个用例重复拼 yaml。
const minimalYAML = `
listen: ":8080"
upstreams:
  agent: "http://agent:8000"
routes:
  - match: "/api/app/"
    upstream: agent
    mode: jwt_c
    rate: 20
    burst: 40
`

func writeCfg(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	p := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
	return p
}

// TestLoadRequiresJWTSecret：生产防裸奔的底线——没给密钥必须拒绝启动。
// 这是 config.Load 里最有价值的一条约束，不能退化。
func TestLoadRequiresJWTSecret(t *testing.T) {
	t.Setenv("JWT_SECRET", "")
	p := writeCfg(t, minimalYAML)

	if _, err := Load(p); err == nil {
		t.Fatal("期望 Load 在 jwt_secret 为空时报错，实际返回 nil")
	}
}

// TestLoadEnvOverridesSecret：密钥从环境变量注入，不落进配置文件。
func TestLoadEnvOverridesSecret(t *testing.T) {
	t.Setenv("JWT_SECRET", "s3cret-from-env")
	p := writeCfg(t, minimalYAML+"jwt_secret: \"written-in-file\"\n")

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.JWTSecret != "s3cret-from-env" {
		t.Fatalf("环境变量未生效: got %q", cfg.JWTSecret)
	}
}

// TestLoadAuditLogDefault：未显式配置审计路径时有兜底，防止审计静默丢失。
func TestLoadAuditLogDefault(t *testing.T) {
	t.Setenv("JWT_SECRET", "x")
	p := writeCfg(t, minimalYAML)

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.AuditLog == "" {
		t.Fatal("audit_log 未设置默认值，审计会无处落盘")
	}
}

// TestLoadRoutesAndUpstream：路由表与上游映射能正确解析（拼写错会导致静默 skip）。
func TestLoadRoutesAndUpstream(t *testing.T) {
	t.Setenv("JWT_SECRET", "x")
	p := writeCfg(t, minimalYAML)

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(cfg.Routes) != 1 {
		t.Fatalf("期望 1 条路由，实际 %d", len(cfg.Routes))
	}
	r := cfg.Routes[0]
	if r.Match != "/api/app/" || r.Mode != "jwt_c" || r.Rate != 20 || r.Burst != 40 {
		t.Fatalf("路由解析不符: %+v", r)
	}
	if u, ok := cfg.UpstreamURL("agent"); !ok || u != "http://agent:8000" {
		t.Fatalf("upstream 解析失败: %q ok=%v", u, ok)
	}
	// 上游不存在时必须返回 false，main 里据此 skip 并告警
	if _, ok := cfg.UpstreamURL("nonexistent"); ok {
		t.Fatal("不存在的 upstream 不应返回 ok=true")
	}
}

// TestRedisEnvOverrides：多副本部署靠环境变量开 Redis 全局限流。
func TestRedisEnvOverrides(t *testing.T) {
	t.Setenv("JWT_SECRET", "x")
	t.Setenv("REDIS_ENABLED", "true")
	t.Setenv("REDIS_ADDR", "redis:6379")
	t.Setenv("REDIS_DB", "3")
	p := writeCfg(t, minimalYAML)

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.Redis.Enabled || cfg.Redis.Addr != "redis:6379" || cfg.Redis.DB != 3 {
		t.Fatalf("redis 环境覆盖失败: %+v", cfg.Redis)
	}
}
