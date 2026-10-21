package config

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// Config 网关运行配置
type Config struct {
	Listen    string            `yaml:"listen"`
	Upstreams map[string]string `yaml:"upstreams"`
	Routes    []Route           `yaml:"routes"`
	JWTSecret string            `yaml:"jwt_secret"`
	AuditLog  string            `yaml:"audit_log"`
	Redis     RedisConfig       `yaml:"redis"`
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
	if c.JWTSecret == "" {
		return nil, fmt.Errorf("jwt_secret 未配置：请通过环境变量 JWT_SECRET 或 config.yaml 的 jwt_secret 字段设置，生产环境禁止留空")
	}
	if c.AuditLog == "" {
		c.AuditLog = "/var/log/gateway/access.log"
	}
	return &c, nil
}

// UpstreamURL 返回路由指向的上游地址
func (c *Config) UpstreamURL(name string) (string, bool) {
	u, ok := c.Upstreams[name]
	return u, ok
}
