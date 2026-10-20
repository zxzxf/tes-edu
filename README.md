# Tes Edu · 课程教育 Agent 系统

基于《课程教育 Agent 系统设计方案》落地的**代码工程**。一套内核、多端复用：学生 App 与 Web 后台都只经 Go 网关，再分流到各自后端。

> 设计文档、架构图、看板图见仓库内 `docx/` 目录（详见文末「文档索引」）。

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 网关 | **Go** | 唯一公网入口：鉴权 / 限流 / 审计 / 路由分发 |
| Agent 后端 | **Python / FastAPI** | Orchestrator + LLM / RAG / OCR 编排（系统"大脑"） |
| Web 管理后台 | **Vue3 + Python/FastAPI** | 教研 / 学情 / 教务中枢（前端由 Nginx 托管，见 web-frontend） |
| 学生端 App | **Kotlin + Jetpack Compose** | 六大能力页 + Room 离线缓存 |
| 数据 | **Kafka + PostgreSQL** | 向量(pgvector) / 图谱(AGE) / 业务表 |

## 目录结构

```
tes-edu/
├── gateway/           # Go 网关（唯一公网入口）
│   ├── main.go
│   ├── internal/
│   │   ├── config/        # 运行配置（环境变量）
│   │   ├── router/        # 路由与反向代理
│   │   └── middleware/    # auth（JWT）/ ratelimit / audit
│   ├── go.mod
│   └── Dockerfile
├── agent-backend/     # Python/FastAPI Agent 后端（C 端系统大脑）
│   ├── main.py
│   ├── app/
│   │   ├── api/solve.py       # /solve /solve_image /similar
│   │   ├── core/              # orchestrator / llm / rag / ocr（占位）
│   │   └── models/schemas.py
│   └── Dockerfile
├── web-backend/       # Python/FastAPI Web 管理后台（B 端运营中枢）
│   ├── main.py
│   ├── app/
│   │   ├── api/         # auth / knowledge / analytics / wrong-questions /
│   │   │                #   exam / grading / reports / compliance
│   │   ├── core/        # rbac / security
│   │   └── models/schemas.py
│   └── Dockerfile
├── web-frontend/      # Vue3 管理后台前端（Nginx 托管，:8081）
│   ├── Dockerfile      # 多阶段：Node 构建 → Nginx 托管
│   ├── nginx.conf      # SPA 回退 + /api 反代网关
│   └── src/                 # router / stores / api / views（登录+布局+7模块页）
├── student-app/       # Android Kotlin/Compose 学生端
│   └── app/src/main/java/com/tes/edu/  # MainActivity / ui / data
├── docx/              # 设计文档与架构图归档
├── docker-compose.yml # 一体化编排（网关/双后端/PG/Kafka）
└── README.md
```

## 架构概览

```
Android(Kotlin) ┐
                ├─ HTTPS ─► Go 网关(鉴权/限流/审计/路由) ─┬─► Agent 后端(:8000) ─► LLM/RAG/OCR
Web 后台(Vue3)  ┘                                        └─► Web 后端(:8001)  ─► PostgreSQL/Kafka
```

- **唯一公网入口**：Go 网关，所有请求先过鉴权与限流，再按路径分流。
- **双后端同栈**：Agent 与 Web 后端均 Python/FastAPI，复用模型与 SDK，仅 2 种运行时。
- **分层边界**：网关只做边界防护与转发，不掺业务逻辑；LLM/RAG/OCR 当前为占位，便于前后端先联调。

## 模块范围（已搭骨架）

- **Go 网关**（P0）：JWT 鉴权 + 全局限流 + 访问审计 + `/api/app`、`/api/admin` 反向代理分流
- **Agent 后端**（P0）：`/solve`、`/solve_image`、`/similar` 路由 + Orchestrator 意图编排 + LLM/RAG/OCR 占位
- **Web 后端**（P2 提前骨架）：auth 登录/RBAC + 教研内容 / 学情看板 / 错题本 / 自动组卷 / 教师批改 / 家长周报 / 合规审核 七大模块路由
- **学生端 App**（P0）：底部导航六大能力页（输入 / 拍照 / 讲解 / 触类 / 图谱 / 背诵）+ Retrofit 接口 + Room 本地库（背诵卡 / 错题本）

> Web 前端（Vue3 骨架在 `web-frontend/`）登录鉴权链路已打通：登录免鉴权、后端签真实 JWT、网关注入身份头，页面结构 + 登录后调接口可联调（数据仍为内存占位）。

## 接口 / 路由清单

### 网关（:8080）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查（无需鉴权） |
| ANY | `/api/app/*` | 转发到 Agent 后端（剥离前缀至 `/`） |
| ANY | `/api/admin/*` | 转发到 Web 后端（剥离前缀至 `/`） |

> 所有 `/api/*` 需携带网关签发的 JWT；网关校验后把 `X-User-Id` / `X-User-Role` 透传给后端。

### Agent 后端（:8000，对外路径前缀 `/api/app`）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/app/solve` | 文本/语音解题与讲解 |
| POST | `/api/app/solve_image` | 拍照识题（multipart 图片） |
| POST | `/api/app/similar` | 触类旁通推荐 |

### Web 后端（:8001，对外路径前缀 `/api/admin`）
| 方法 | 路径 | 模块 | 角色 |
|---|---|---|---|
| POST | `/api/admin/login` | 登录 | 全部 |
| GET | `/api/admin/me` | 当前用户 | 全部 |
| GET/POST | `/api/admin/knowledge` | 教研内容管理 | admin / researcher |
| GET | `/api/admin/analytics/summary` | 学情看板 | admin / teacher / parent |
| GET/POST | `/api/admin/wrong-questions` | 错题本 | admin / teacher / parent（POST 限 admin/teacher） |
| POST | `/api/admin/exam/generate` | 自动组卷 | admin / teacher / researcher |
| POST/PUT | `/api/admin/grading/submit` · `/grading/{id}` | 教师批改 | admin / teacher |
| GET | `/api/admin/reports/weekly` | 家长周报 | admin / teacher / parent |
| GET/POST/PUT | `/api/admin/compliance` · `/compliance/{id}/review` | 合规审核 | admin / researcher |

### 学生端 App
- `ApiService`（Retrofit）契约对齐 `/api/app/*`，包括文本解题、拍照解题、知识图谱、背诵卡/错题本本地（Room）。

## 本地启动

```bash
# 1) Agent 后端（:8000）
cd agent-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 2) Web 后端（:8001）
cd web-backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
# 后台交互文档： http://localhost:8001/docs

# 3) Go 网关（:8080，需先 go mod tidy 生成 go.sum）
cd gateway
go run .

# 4) 学生端 App
#    用 Android Studio 打开 student-app/，运行到模拟器
#    网关地址默认 http://10.0.2.2:8080（模拟器访问宿主机）
```

## 环境变量

**网关 `gateway`（通过环境变量注入，默认值见 `internal/config/config.go`）**
| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8080` | 网关监听端口 |
| `JWT_SECRET` | `change-me-in-prod` | JWT 校验密钥（生产务必修改） |
| `AGENT_BACKEND_URL` | `http://localhost:8000` | Agent 后端地址 |
| `WEB_BACKEND_URL` | `http://localhost:8001` | Web 后端地址 |
| `RATE_LIMIT` | `20` | 单 IP 每秒令牌桶容量 |

**后端 `agent-backend` / `web-backend`**
- 当前以占位为主，接入真实能力时建议补充：`LLM_API_KEY`、`LLM_BASE_URL`、`REDIS_URL`、`DATABASE_URL`、`KAFKA_BROKERS` 等（在各自 `app/core/` 配置中读取）。

## 数据层（设计参考）

**PostgreSQL**（详见 `docx/` 方案文档 Schema 章节）
- `knowledge_points` 知识点、`questions` 题库、`embeddings` 向量、`kg_edges` 知识图谱边、`drill_cards` 背诵卡、`student_logs` 学情日志
- 向量检索需 **pgvector（PG 13+）**；知识图谱用 **Apache AGE**（或独立 Neo4j）
- 当前 PG 11.2 环境无法装 pgvector，建议新起 PG 16 承载向量+图谱

**Kafka**（异步管道，非数据源）
- `course_kb_ingest`（知识库入库）、`drill_sync`（背诵卡同步）、`audit_queue`（审计）、`agent_events`（Agent 事件）

## 部署

各模块均已提供 `Dockerfile`，仓库根目录含可运行的 **`docker-compose.yml`**（网关 / 双后端 / PostgreSQL / Kafka 一体化编排）：

```bash
docker compose up --build
```

- 网关 `:8080` 对外；`agent-backend` / `web-backend` 仅内网可达；`web-frontend` 经 Nginx 托管在 `:8081`，其页面内 `/api` 请求由 Nginx 反代到网关 `:8080`。
- `postgres` 用 `pgvector/pgvector:pg16`（向量 + 图谱底座）；`kafka` 用 bitnami KRaft 模式。
- 当前后端为占位实现，未强制依赖 PG/Kafka，可独立启动。

## 路线图（对应《应用任务看板图》）

| 阶段 | 内容 |
|---|---|
| **P0** | 网关 + Agent 后端 + App 基础壳 + RBAC（已搭骨架） |
| **P1** | 后端补全 RAG / 触类旁通 / 拍照；App 补全拍照与图谱页 |
| **P2** | Web 后端骨架（已提前搭出 7 大模块路由）+ 前端 Vue3 + 学情看板 / 错题本 |
| **P3** | Web 后台：组卷 / 教师端 / 互动 / 多模态；可选小程序 |

## 文档索引（`docx/`）

- `课程教育Agent设计方案.md` — 总体架构 / 需求实现 / 技术栈 / Schema / 代码骨架 / 市场对比 / 图谱建图 / 路线图 / 技术栈与语言分工
- `课程教育Agent完整方案书.md` — 上述方案的合并版
- `Web管理后台功能设计方案.md` — Web 后台 10 模块详细设计
- `应用拆分与开发分期.md` — 应用拆分与 P0–P3 分期
- `整体系统架构图.png / .html` — 系统整体架构
- `课程教育Agent架构图.html` — 含 Go 网关层的分层架构
- `Web管理后台功能结构图.html` — 后台模块结构
- `安卓功能架构图.html` — 客户端架构
- `应用全景图.html` / `应用任务看板图.html` — 应用拆分与看板
