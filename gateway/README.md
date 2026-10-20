# Tes Edu 网关（Go）— P0

唯一公网入口，负责**鉴权 / 限流 / 审计 / 路由分发**。后端服务（Agent 后端、Web 后端）不直接暴露公网。

## 职责
- **鉴权**：校验 JWT，解析 `uid` / `role`（RBAC 四角色：student / teacher / researcher / parent / admin）
- **限流**：全局令牌桶（按 `RATE_LIMIT` 配置，rps）
- **审计**：所有请求留痕（IP / 路径 / 状态码 / 用户 / 耗时），承接合规要求
- **路由**：`/api/app/*` → Agent 后端（C 端）；`/api/admin/*` → Web 后端（B 端，P2/P3 接入）

## 目录
```
gateway/
├── main.go                  # 入口
├── internal/
│   ├── config/              # 环境变量配置
│   ├── router/              # 路由 + 反向代理
│   └── middleware/          # auth / ratelimit / audit
├── go.mod
└── Dockerfile
```

## 运行
```bash
export JWT_SECRET=xxx
export AGENT_BACKEND_URL=http://localhost:8000
export WEB_BACKEND_URL=http://localhost:8001
go run .
# 或
docker build -t tes-edu-gateway . && docker run -p 8080:8080 tes-edu-gateway
```

> 说明：`go.mod` 中 gin/jwt/rate 依赖需在可联网环境执行 `go mod tidy` 生成 `go.sum` 后方可编译。
