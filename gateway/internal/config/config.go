package config

import (
	"os"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Listen    string            `yaml:"listen"`
	Upstreams map[string]string `yaml:"upstreams"`
	Routes    []Route           `yaml:"routes"`
	JWT       JWTConfig         `yaml:"jwt"`
	RateLimit RateLimit         `yaml:"ratelimit"`
	AuditLog  string            `yaml:"audit_log"`
}

type Route struct {
	Match    string `yaml:"match"`
	Upstream string `yaml:"upstream"`
	Auth     string `yaml:"auth"` // "jwt_c" | "rbac" | "" (none)
}

type JWTConfig struct {
	Secret string `yaml:"secret"`
}

type RateLimit struct {
	RPS   float64 `yaml:"rps"`
	Burst int     `yaml:"burst"`
}

func Load(path string) (*Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := yaml.Unmarshal(b, &c); err != nil {
		return nil, err
	}
	// 允许用环境变量覆盖 JWT secret（生产建议）
	if v := os.Getenv("JWT_SECRET"); v != "" {
		c.JWT.Secret = v
	}
	if c.JWT.Secret == "" {
		c.JWT.Secret = "CHANGE_ME_IN_PROD"
	}
	return &c, nil
}

func (c *Config) UpstreamURL(name string) (string, bool) {
	u, ok := c.Upstreams[name]
	return u, ok
}
