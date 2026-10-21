package config

import (
	"fmt"
	"os"
	"strconv"

	"gateway/internal/allowlist"
	"gopkg.in/yaml.v3"
)

// Config 网关运行配置
type Config struct {
	Listen       string                   `yaml:"listen"`
	Upstreams    map[string]string        `yaml:"upstreams"`
	Routes       []Route                  `yaml:"routes"`
	Breakers     map[string]BreakerConfig `yaml:"breakers"`
	LLMAllowlist LLMAllowlistConfig       `yaml:"llm_allowlist"`
	JWTSecret    string                   `yaml:"jwt_secret"`
	AuditLog     string                   `yaml:"audit_log"`
	Redis        RedisConfig              `yaml:"redis"`
}

// Route 单条路由规则
//   - Match    : 前缀匹配（如 /api/app/），命中后转发到 Upstream
//   - Upstream : upstreams 映射中的键
//   - Mode     : 鉴权模式，"jwt_c"=C 端用户态校验；"rbac"=B 端管理员校验；""=不鉴权
//   - Roles    : mode=rbac 时的允许角色白名单
//   - Rate/Burst: 令牌桶限流（请求/秒，突发容量）
type Route struct {
	Match    string   `yaml:"match"`
	Upstream string   `yaml:"upstream"`
	Mode     string   `yaml:"mode"`
	Roles    []string `yaml:"roles"`
	Rate     float64  `yaml:"rate"`
	Burst    int      `yaml:"burst"`
}

// RedisConfig 限流用的 Redis 配置（多副本全局令牌桶）。
// 当 Enabled=false 时，网关使用进程内 per-key 令牌桶（仅单副本一致）。
type RedisConfig struct {
	Enabled      bool   `yaml:"enabled"`
	Addr         string `yaml:"addr"`
	Password     string `yaml:"password"`
	DB           int    `yaml:"db"`
	KeyPrefix    string `yaml:"key_prefix"`
	DialTimeout  int    `yaml:"dial_timeout_ms"`
	ReadTimeout  int    `yaml:"read_timeout_ms"`
	WriteTimeout int    `yaml:"write_timeout_ms"`
}

// BreakerConfig 单上游熔断配置（三态：Closed/Open/HalfOpen）。
// 未配置的字段回退默认值：enabled=true、failure_threshold=5、cooldown_seconds=10。
type BreakerConfig struct {
	Enabled          bool `yaml:"enabled"`
	FailureThreshold int  `yaml:"failure_threshold"`
	CooldownSeconds  int  `yaml:"cooldown_seconds"`
}

// LLMAllowlistConfig LLM 模型 endpoint 白名单（P5 合同硬要求：
// 仅接受 OpenAI 兼容 /v1 接口，白名单外 endpoint 由网关拒绝）。
// agent-backend 在 LLM 绑定请求头（默认 X-LLM-Endpoint）声明目标端点，
// 网关按白名单校验；未带该头的请求默认放行（非 LLM 流量）。
type LLMAllowlistConfig struct {
	Enabled       bool     `yaml:"enabled"`
	Header        string   `yaml:"header"`
	RequireHeader bool     `yaml:"require_header"`
	Endpoints     []string `yaml:"endpoints"`
}

// Load 读取 yaml 并允许用环境变量 JWT_SECRET 覆盖签名密钥
func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := yaml.Unmarshal(b, &c); err != nil {
		return nil, err
	}
	if v := os.Getenv("JWT_SECRET"); v != "" {
		c.JWTSecret = v
	}
	if v := os.Getenv("REDIS_ADDR"); v != "" {
		c.Redis.Addr = v
	}
	if v := os.Getenv("REDIS_PASSWORD"); v != "" {
		c.Redis.Password = v
	}
	if v := os.Getenv("REDIS_ENABLED"); v != "" {
		c.Redis.Enabled = v == "true" || v == "1" || v == "yes" || v == "on"
	}
	if v := os.Getenv("REDIS_DB"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			c.Redis.DB = n
		}
	}
	if v := os.Getenv("REDIS_KEY_PREFIX"); v != "" {
		c.Redis.KeyPrefix = v
	}
	if c.JWTSecret == "" {
		return nil, fmt.Errorf("jwt_secret 未配置：请通过环境变量 JWT_SECRET 或 config.yaml 的 jwt_secret 字段设置，生产环境禁止留空")
	}
	// 审计日志路径优先级：环境变量 GATEWAY_LOG（容器部署用）> config.yaml 的
	// audit_log > 默认 /var/log/gateway/access.log。
	// 修复记录：此前两份 compose 均设置 GATEWAY_LOG，但代码从不读取，为死配置。
	if v := os.Getenv("GATEWAY_LOG"); v != "" {
		c.AuditLog = v
	}
	if c.AuditLog == "" {
		c.AuditLog = "/var/log/gateway/access.log"
	}
	// LLM 白名单头名与环境变量覆盖（endpoint 清单只在 yaml 配置，
	// 避免把合同端点写进环境变量导致多副本漂移）
	if v := os.Getenv("LLM_ALLOWLIST_ENABLED"); v != "" {
		c.LLMAllowlist.Enabled = v == "true" || v == "1" || v == "yes" || v == "on"
	}
	if v := os.Getenv("LLM_ALLOWLIST_HEADER"); v != "" {
		c.LLMAllowlist.Header = v
	}
	if v := os.Getenv("LLM_ALLOWLIST_REQUIRE_HEADER"); v != "" {
		c.LLMAllowlist.RequireHeader = v == "true" || v == "1" || v == "yes" || v == "on"
	}
	// 白名单启用时启动即校验端点配置：写错必须 fail fast，
	// 不能静默变成"放行所有"（allowlist.ParseEndpoints 对非法 URL 报错）
	if c.LLMAllowlist.Enabled {
		if _, err := allowlist.ParseEndpoints(c.LLMAllowlist.Endpoints); err != nil {
			return nil, fmt.Errorf("llm_allowlist.endpoints 配置错误: %w", err)
		}
	}
	return &c, nil
}

// UpstreamURL 返回路由指向的上游地址
func (c *Config) UpstreamURL(name string) (string, bool) {
	u, ok := c.Upstreams[name]
	return u, ok
}

// BreakerFor 返回指定上游的熔断配置；未配置时回退默认值（启用 / 5 次 / 10s）。
// 默认启用是有意为之：熔断是方案书 §十 明列的网关职责，漏配不应等于裸奔。
func (c *Config) BreakerFor(name string) BreakerConfig {
	b := BreakerConfig{Enabled: true, FailureThreshold: 5, CooldownSeconds: 10}
	if cfg, ok := c.Breakers[name]; ok {
		if cfg.FailureThreshold > 0 {
			b.FailureThreshold = cfg.FailureThreshold
		}
		if cfg.CooldownSeconds > 0 {
			b.CooldownSeconds = cfg.CooldownSeconds
		}
		b.Enabled = cfg.Enabled
	}
	return b
}
