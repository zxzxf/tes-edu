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

// TestLoadRequiresJWTPublicKey：生产防裸奔的底线——没给公钥必须拒绝启动。
// 这是 config.Load 里最有价值的一条约束，不能退化。
func TestLoadRequiresJWTPublicKey(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "")
	p := writeCfg(t, minimalYAML)

	if _, err := Load(p); err == nil {
		t.Fatal("期望 Load 在 jwt_public_key 为空时报错，实际返回 nil")
	}
}

// TestLoadEnvOverridesJWTPublicKey：公钥从环境变量注入，不落进配置文件。
func TestLoadEnvOverridesJWTPublicKey(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "pub-from-env")
	p := writeCfg(t, minimalYAML+"jwt_public_key: \"written-in-file\"\n")

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.JWTPublicKey != "pub-from-env" {
		t.Fatalf("环境变量未生效: got %q", cfg.JWTPublicKey)
	}
}

// TestLoadAuditLogDefault：未显式配置审计路径时有兜底，防止审计静默丢失。
func TestLoadAuditLogDefault(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
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
	t.Setenv("JWT_PUBLIC_KEY", "x")
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
	t.Setenv("JWT_PUBLIC_KEY", "x")
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

// TestLLMAllowlistValidation：启用白名单但端点配置非法必须启动即失败
// （fail fast，不能静默变成放行所有）。
func TestLLMAllowlistValidation(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
	p := writeCfg(t, minimalYAML+`
llm_allowlist:
  enabled: true
  endpoints:
    - "api.deepseek.com"
`)
	if _, err := Load(p); err == nil {
		t.Fatal("白名单端点非法应报错")
	}

	// 合法端点 + 关闭时应通过（关闭则不校验）
	p2 := writeCfg(t, minimalYAML+`
llm_allowlist:
  enabled: false
  endpoints:
    - "api.deepseek.com"
`)
	if _, err := Load(p2); err != nil {
		t.Fatalf("关闭时不应校验端点: %v", err)
	}
}

// TestLLMAllowlistEnvOverride：环境变量可覆盖开关/头名/强制头。
func TestLLMAllowlistEnvOverride(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
	t.Setenv("LLM_ALLOWLIST_ENABLED", "true")
	t.Setenv("LLM_ALLOWLIST_HEADER", "X-Model-Endpoint")
	t.Setenv("LLM_ALLOWLIST_REQUIRE_HEADER", "true")
	p := writeCfg(t, minimalYAML)

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if !cfg.LLMAllowlist.Enabled ||
		cfg.LLMAllowlist.Header != "X-Model-Endpoint" ||
		!cfg.LLMAllowlist.RequireHeader {
		t.Fatalf("llm_allowlist 环境覆盖失败: %+v", cfg.LLMAllowlist)
	}
}

// TestBreakerForDefaults：未配置熔断的上游回退默认值（启用/5次/10s）。
// 默认启用是有意为之：漏配熔断不应等于裸奔（方案书 §十 明列网关职责）。
func TestBreakerForDefaults(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
	p := writeCfg(t, minimalYAML)

	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	b := cfg.BreakerFor("agent_backend")
	if !b.Enabled || b.FailureThreshold != 5 || b.CooldownSeconds != 10 {
		t.Fatalf("默认熔断配置不符: %+v", b)
	}
}

// TestBreakerForExplicit：显式配置优先，enabled=false 可关闭。
func TestBreakerForExplicit(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
	p := writeCfg(t, minimalYAML+`
breakers:
  agent_backend:
    enabled: false
    failure_threshold: 3
    cooldown_seconds: 30
`)
	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	b := cfg.BreakerFor("agent_backend")
	if b.Enabled || b.FailureThreshold != 3 || b.CooldownSeconds != 30 {
		t.Fatalf("显式熔断配置未生效: %+v", b)
	}
	// 未列出的上游仍走默认
	other := cfg.BreakerFor("web_backend")
	if !other.Enabled || other.FailureThreshold != 5 {
		t.Fatalf("未列出上游应走默认: %+v", other)
	}
}

// TestGatewayLogEnvOverride：GATEWAY_LOG 环境变量必须生效（修复死配置）。
// 此前两份 compose 均设置 GATEWAY_LOG 但代码从不读取，审计路径实际走
// config.yaml 的 audit_log，容器内卷挂载点与配置不一致。
func TestGatewayLogEnvOverride(t *testing.T) {
	t.Setenv("JWT_PUBLIC_KEY", "x")
	t.Setenv("GATEWAY_LOG", "/var/log/gateway/from-env.log")

	// 优先级 1：环境变量覆盖 yaml
	p := writeCfg(t, minimalYAML+"audit_log: \"/from/yaml.log\"\n")
	cfg, err := Load(p)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.AuditLog != "/var/log/gateway/from-env.log" {
		t.Fatalf("GATEWAY_LOG 应覆盖 yaml: got %q", cfg.AuditLog)
	}

	// 优先级 2：无环境变量时用 yaml 值
	t.Setenv("GATEWAY_LOG", "")
	p2 := writeCfg(t, minimalYAML+"audit_log: \"/from/yaml.log\"\n")
	cfg2, err := Load(p2)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg2.AuditLog != "/from/yaml.log" {
		t.Fatalf("无环境变量时应取 yaml 值: got %q", cfg2.AuditLog)
	}

	// 优先级 3：都没有时用默认值
	p3 := writeCfg(t, minimalYAML)
	cfg3, err := Load(p3)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg3.AuditLog != "/var/log/gateway/access.log" {
		t.Fatalf("兜底默认值不符: got %q", cfg3.AuditLog)
	}
}
