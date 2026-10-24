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

`web-frontend/` 已提供多阶段 `Dockerfile` + `nginx.conf`。

**生产（137，T-E1 2026-10-24 上线）**：管理后台挂在 **`/admin/` 子路径**（经 edu-lb 剥前缀反代到 `edu-web-front` 容器）：

- `vite.config.js` `base: '/admin/'` + `router` `createWebHistory('/admin/')`——地址栏、资源引用、路由链接均带 `/admin/` 前缀；
- 页面 API 走 `/api/admin/*`（lb → 网关 → web-backend），**不经**前端容器；
- 访问入口：`http(s)://<域名或IP>/admin/`（`/admin` 自动 301 补尾斜杠）。

```bash
# 137 上重建（源码已同步到 /opt/tes-edu-web-front/）：
docker build -t edu-web-front:real /opt/tes-edu-web-front
# 换容器 + 刷新 lb 上游（容器重建后 IP 变化，必须 HUP，详见 deploy/README 关键约束 7）
cd /opt/tes-edu-gateway && docker compose up -d web-front && docker kill -s HUP edu-lb
```

本地开发仍可用 `npm run dev`（base 对 dev 同样生效，访问 `http://localhost:5173/admin/`）；`/api` 由 Vite 代理到本地网关。

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
