# Web 管理后台（web-backend）

B 端运营中枢的**后端服务**（FastAPI）。前端（Vue3）在 P2/P3 接入；本服务先提供全部后台 API 骨架，与《Web管理后台功能设计方案》对齐。

## 模块（对应设计文档）

| 路由前缀 | 模块 | 角色 |
|---|---|---|
| `/auth` | 登录 / 当前用户 | 全部 |
| `/knowledge` | 教研内容管理 | admin / researcher |
| `/analytics` | 学情看板 | admin / teacher / parent |
| `/wrong-questions` | 错题本 | admin / teacher / parent |
| `/exam` | 自动组卷 | admin / teacher / researcher |
| `/grading` | 教师批改 | admin / teacher |
| `/reports` | 家长周报 | admin / teacher / parent |
| `/compliance` | 合规审核 | admin / researcher |

## 鉴权

- 公网请求先过 **Go 网关**（JWT 校验 + 限流 + 审计）。
- 网关把用户身份以 `X-User-Id` / `X-User-Role` 头透传到本服务（内网信任）。
- 角色见 `app/core/rbac.py`：admin / researcher / teacher / parent / student。

## 启动

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
# 交互式文档： http://localhost:8001/docs
```

网关路由：`/api/admin/*` → 本服务（前缀已由网关剥离，本地直连时前缀为 `/api/admin`）。

> 占位实现：所有数据存内存列表，P2 接入 PostgreSQL（复用 Agent 后端同库或独立 schema）。
