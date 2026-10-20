package config

import (
	"os"
	"strconv"
)

// Config 网关运行配置（优先读环境变量）
type Config struct {
	Port            string
	JWTSecret       string
	AgentBackendURL string // C 端 Agent 后端（FastAPI）
	WebBackendURL   string // B 端 Web 后端（FastAPI）
	RateLimit       float64
}

func Load() *Config {
	return &Config{
		Port:            getenv("PORT", "8080"),
		JWTSecret:       getenv("JWT_SECRET", "change-me-in-prod"),
		AgentBackendURL: getenv("AGENT_BACKEND_URL", "http://localhost:8000"),
		WebBackendURL:   getenv("WEB_BACKEND_URL", "http://localhost:8001"),
		RateLimit:       getenvf("RATE_LIMIT", 20),
	}
}

func getenv(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func getenvf(k string, d float64) float64 {
	if v := os.Getenv(k); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return d
}
