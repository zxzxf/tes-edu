# Web 管理后台前端（web-frontend）

Vue 3 + Vite 前端骨架，对应 `web-backend` 提供的后台 API。页面已按模块对接各接口，但后端当前为占位实现（内存数据），需先启动网关与后端才能联调。

## 技术栈

- **Vue 3** + **Vite**
- **vue-router 4**：路由 + 登录守卫
- **Pinia**：auth 登录态（token 存 localStorage）
- **axios**：请求封装，自动附带 `Authorization: Bearer <token>`

## 模块页面

| 路由 | 页面 | 对应后端模块 |
|---|---|---|
| `/dashboard` | 概览 | — |
| `/knowledge` | 教研内容管理 | knowledge |
| `/analytics` | 学情看板 | analytics |
| `/wrong-questions` | 错题本 | wrong-questions |
| `/exam` | 自动组卷 | exam |
| `/grading` | 教师批改 | grading |
| `/reports` | 家长周报 | reports |
| `/compliance` | 合规审核 | compliance |

## 启动

```bash
npm install
npm run dev        # http://localhost:5173
```

开发服务器通过 Vite 代理把 `/api` 转发到 Go 网关（http://localhost:8080），再由网关分流到 web-backend。

## 容器化（生产/演示）

`web-frontend/` 已提供多阶段 `Dockerfile` + `nginx.conf`，仓库根 `docker-compose.yml` 中包含 `web-frontend` 服务（端口 `:8081`）：

```bash
docker compose up --build      # 启动后访问 http://localhost:8081
```

Nginx 托管构建产物，并把页面内的 `/api` 请求反代到网关 `gateway:8080`（容器内服务名互访），SPA 子路由刷新自动回退到 `index.html`。

## 接口约定

- 所有请求 `baseURL = /api/admin`，与后端 `main.py` 的路由前缀一致。
- 登录：`POST /api/admin/login`，返回 `access_token`；后续请求在 `Authorization` 头携带。
- 角色控制：路由 `meta.role` 仅做前端展示约束；真实权限由网关 + 后端 RBAC 校验。

## 演示账号

`admin@tes.edu / demo1234`（参见 web-backend 占位用户库）

## 说明

本骨架**未接入真实数据库**，页面数据来自后端内存占位。登录鉴权链路已打通：
1. 网关已放行 `/api/admin/login`（无需 token 即可登录）；
2. 后端 `auth.login` 用 `python-jose` 以 HS256 签发真实 JWT（载荷含 `uid` / `role`，与网关 `JWT_SECRET` 一致）；
3. 网关在鉴权通过后向 web-backend 注入 `X-User-Id` / `X-User-Role` 头，后端据此做 RBAC。

因此页面结构 + 登录后调接口已可联调；后续只需把内存占位换成真实 PostgreSQL/Kafka 即可上线。
