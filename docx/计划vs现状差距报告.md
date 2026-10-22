# Tes Edu 计划 vs 现状差距报告

> 编写日期：2026-10-21
> 数据来源：本地 `tes-edu/docker-compose.yml` 与 `docx/` 设计文档（计划侧）；`124.221.253.137` 服务器 `docker ps -a` 与容器内进程（现状侧）
> 结论先行：**计划、文档、代码骨架三者自洽（都停在 P0 骨架），但"部署形态"与计划存在系统性偏差；同时工程化（安全/合规/可观测/CI/CD/测试）几乎为空。**

---

## 1. 背景与目的

本项目的目标被反复概括为 **"一套内核、多端复用"**：一个 Go 网关（唯一公网入口）+ Agent 后端（AI 大脑）+ Web 后端（B 端运营），支撑学生端 App 与 Web 管理后台，数据层用 Kafka + PostgreSQL（向量 pgvector / 图谱 AGE）。

本报告把"计划里应该有的样子"和"服务器上真实跑的样子"逐项对照，定位差距、分析根因，并给出按优先级排序的收敛路线。

---

## 2. 计划侧：系统应有的样子

依据 `docker-compose.yml` 与 `docx/` 设计文档，完整形态如下：

| 层 | 组件 | 端口 | 说明 |
|---|---|---|---|
| 接入层 | `gateway`（Go） | 8080 对外 | JWT 鉴权 / 限流 / 审计 / `/api/app`、`/api/admin` 分流 |
| AI 编排层 | `agent-backend`（FastAPI） | 8000 内网 | `/solve`、`/solve_image`、`/similar` 三能力 + Orchestrator |
| B 端中枢 | `web-backend`（FastAPI, 7 模块） | 8001 内网 | 教研 / 学情 / 错题 / 组卷 / 批改 / 周报 / 合规 |
| 前端 | `web-frontend`（Vue3 + Nginx） | 8081→80 | 管理后台界面 |
| 数据层 | `postgres`（pgvector/pgvector:pg16） | 5432 | 向量检索（RAG）底座 |
| 异步管道 | `kafka`（bitnami/kafka:3.7, **KRaft 单节点**） | 9092 | 消息总线，无 ZooKeeper |

开发分期（来自设计文档）：**P0 后端骨架 → P1 双端 → P2 可选跨端 → P3 增强**。分期依赖顺序正确（Agent 后端是地基，两端长在它上面）。

`docker-compose.yml` 中安全相关配置（原文）：
```yaml
JWT_SECRET: "change-me-in-prod"   # gateway 与 web-backend 均硬编码
RATE_LIMIT: "20"                  # 全局令牌桶，无用户级配额
```

---

## 3. 现状侧：服务器真实部署

> **复核更新（2026-10-23）**：原报告（2026-10-21）仅描述 `124.221.253.137` 单机；实测存在**第二台服务器 `106`**（同账号，承载数据层）。以下为 2026-10-23 实测最新状态——原快照中"age Up 7h""无 postgres"等断言已过时。

`124.221.253.137` 上实际运行的容器（含已停止）：

| 容器名 | 镜像 | 状态 | 端口 | 备注 |
|---|---|---|---|---|
| `edu-gateway` | `…/mis_ser/edu-gateway:latest` | Up 4h | 80→8080 | 进程 `/app/gateway`，入口正常 |
| `edu-mock-backend` | `…/mis_ser/edu-mock-backend:latest` | Up 4h | 8000 | 进程 `/app/mock`，**是 Mock 桩，非 web-backend** |
| `kafka` | `…/mis_ser/kafka:3.8.0` | Up 6h | 9092→9094 | **ZK 模式** |
| `zookeeper` | `…/mis_ser/zookeeper:3.9` | Up 6h | 2181 | 独立 ZK，单点 |
| ~~`age`~~ | `…/mis_ser/apache-age` | **已停止/不存在** | — | 图谱库；**2026-10-23 实测 137 无任何 age 容器在跑**（P0 DoD"AGE 初始化"暂未满足） |
| `kafka-init` | `…/mis_ser/kafka:3.8.0` | Exited(0) 6h | — | 一次性初始化（正常） |
| `optimistic_payne` | `hello-world` | Exited(0) 10d | — | 测试容器，可清理 |
| `cloudflared` / `cf-quick` | `cloudflare/cloudflared` | Up | — | Cloudflare Tunnel（HTTPS 接入，与业务无关） |

**关键事实确认**：
- `edu-mock-backend` 容器内进程是 `/app/mock` —— 证实服务器跑的是**模拟后端桩**，不是设计文档里那个 7 模块的 FastAPI `web-backend`。
- 镜像全部来自 `registry.cn-shanghai.aliyuncs.com/mis_ser/`（阿里云容器registry），**不是本地 `build:` 构建** —— 说明存在一套"构建→推镜像→拉取运行"的流水线，但 `docker-compose.yml` 里完全没体现（`build: ./xxx` 与线上镜像来源不一致）。
- 137 **没有 `postgres/pgvector` 容器**，也没有独立真实 `agent-backend` 容器、没有 `web-frontend` 容器；`apache-age` 当前亦未运行（见上方 age 行）。
- **新增 `106` 数据层服务器**：`srv-postgres`（`pgvector/pgvector:pg16`，**向量底座已在**）+ `srv-redis`（**137 的 Redis 从库，`master_link_status:up`**）。数据层实际跨 137/106 两台机器，原报告仅画了 137，**遗漏 106 是现状基线最大的结构性盲区**。

---

## 4. 计划 vs 现状 差距对照（核心）

| # | 维度 | 计划（compose/文档） | 现状（服务器） | 偏差等级 | 影响 |
|---|---|---|---|---|---|
| G1 | 关系型/向量库 | `pgvector/pgvector:pg16`（向量底座，P1 RAG 核心） | 106 已有 `srv-postgres`(pg16+pgvector)；137 无 postgres；`age` 当前未运行 | 🟠 重要 | 向量底座已具备（在 106），但**后端未连接**（仍内存占位）；图谱库 AGE 暂缺，P0 DoD"AGE 初始化"未满足 |
| G2 | Kafka 模式 | bitnami KRaft 单节点（无 ZK） | `kafka:3.8.0` + 独立 `zookeeper:3.9`（ZK 模式） | 🟠 重要 | 部署形态与设计不符；ZK 是单点 SPOF |
| G3 | Web 后端 | `web-backend`（FastAPI 7 模块） | `edu-mock-backend`（`/app/mock` 桩） | 🔴 严重 | 真实业务逻辑未部署，仅是联调桩 |
| G4 | Agent 后端 | 独立 `agent-backend` 服务 | 无独立容器（疑似并入 mock 或未部署） | 🟠 重要 | AI 能力（RAG/OCR/VLM/编排）未独立可部署 |
| G5 | 前端 | `web-frontend`（Vue3，:8081） | 未部署 | 🟡 渐进 | 无管理界面容器 |
| G6 | 镜像来源 | `build: ./gateway` 等本地构建 | 阿里云 ACR 预构建镜像 | 🟠 重要 | 缺 CI 构建/推送环节，compose 不可一键复现线上 |
| G7 | 网关端口 | 计划 8080 对外 | 实际 80→8080（Nginx/前置已介入） | 🟢 正常 | 端口映射差异，可接受 |
| G8 | 安全密钥 | `JWT_SECRET: change-me-in-prod`（硬编码） | 网关已升级 **RS256**（公钥经 env 注入，无硬编码对称密钥） | ✅ 已解除 | 原🔴随 RS256 改造消除；网关侧另已落地白名单拒绝 / 心跳回连 / 匿名指标回传三项合同硬要求 |
| G9 | 密码登录 | 未提及 | 服务器 `PasswordAuthentication yes` | 🔴 严重 | 密钥已通但密码仍开，锁门风险 |
| G10 | 限流 | 全局 `RATE_LIMIT=20` | 沿用 | 🟡 渐进 | 无用户级配额/计费管控 |

---

## 5. 工程化缺口（与机器数量无关）

即便只有一台服务器，以下也是"能否上线"的门槛，目前几乎为空：

| 维度 | 企业落地需要 | 现状 | 等级 |
|---|---|---|---|
| 安全 | TLS 全链路、密钥进 Vault/KMS、默认密钥必改 | 明文 HTTP(:80)；**JWT 已改 RS256（无默认对称密钥）**；密码登录仍开 | 🟠 |
| 合规（教育+未成年人） | 内容审核、PII 脱敏、数据留存策略 | 仅有"合规审核"路由桩，无实际逻辑 | 🔴 |
| 可观测 | 日志聚合、指标、链路追踪、告警 | 仅审计日志占位 + `/healthz`；无 metrics/tracing/alert | 🟠 |
| CI/CD | 自动构建/测试/灰度/回滚、多环境 | 无 CI；手动拉镜像 run；构建来源不在 compose | 🟠 |
| 测试 | 单测/集成/契约测试 | 零测试文件 | 🟠 |
| 数据层生产化 | 迁移工具、备份、Schema 版本化 | 无迁移脚本(Alembic/Flyway)；无备份；AGE 版本未对齐 | 🟠 |
| 韧性 | 依赖失败兜底、重试、幂等、降级 | 无降级/重试/超时治理 | 🟡 |

---

## 6. 根因分析

1. **部署滞后于设计**：当前服务器处于"早期联调骨架"——网关 + 一个 mock 后端 + 消息/图谱底座，目的是打通链路，尚未演进到文档描述的完整多服务形态。这与 P0 骨架定位一致，属正常阶段性状态，**但文档未标注"当前为 mock 阶段"**，易让人误以为已具备真实后端。
2. **compose 与线上脱节**：compose 描述的是"理想蓝图"（本地 build、KRaft、pgvector），线上是另一套（ACR 镜像、ZK、AGE）。两描述并行、互不引用，导致"计划"和"现状"无法直接对齐。
3. **非功能需求缺位**：设计文档聚焦功能架构与分期，缺少工程化章节（安全、合规、可观测、CI/CD、测试、数据迁移），使得"计划"本质是技术分期而非项目计划书（无排期、责任人、验收标准）。

---

## 7. 建议收敛路线（按优先级）

**P0 — 上线前必做（否则一票否决）**
- ~~**改 `JWT_SECRET`**~~（**已完成**：网关升级 RS256，公钥经 env 注入，无对称默认密钥；详见 §4 G8）：保留以下剩余项。
- **关服务器密码登录**：密钥已验证可用（`ssh 137` 免密），改 `PasswordAuthentication no` 后保留 Web 控制台兜底。
- **TLS 加密公网入口**：当前无域名，先用 Cloudflare Quick Tunnel（已通，临时地址 `https://identification-accommodate-nerve-carl.trycloudflare.com`）或自签证书 + Nginx；有域名后走固定 Tunnel。
- **合规真实逻辑**：未成年人内容审核、PII 脱敏、数据留存策略。

**P1 — 让计划与现状对齐**
- 明确数据层真相：用 `pg16+pgvector` 承载向量、用 `apache-age` 承载图谱，两者并存（文档方向一致）；补齐 G1。
- 统一部署形态：把 `edu-mock-backend` 演进为文档里的 `web-backend`，补齐独立 `agent-backend` 与 `web-frontend`；让 compose 真正可一键 `up`（对齐 G2–G5）。
- 镜像来源收口：引入 CI 构建并推到 ACR，compose 用 `image:` 引用，消除 G6 的"双描述"问题。

**P2 — 稳定运行与规模化**
- Kafka 改集群（≥3 broker + 奇数 ZK / 或 KRaft），加死信队列；PostgreSQL/AGE 主从 + 每日 `pg_dump` 备份。
- 引入 Prometheus + Loki + 基础告警，区分 readiness/liveness 探针。
- 补单测与契约测试（重点：网关鉴权、Agent 输出 Schema、计费链路）；加 Alembic 迁移、用户级限流/配额、LLM 成本监控与降级。

---

## 8. 待确认 / 待办清单

- [x] `JWT_SECRET` 替换为随机值 → **已完成**：网关升级 **RS256**（公钥 env 注入，无默认对称密钥），原 G8 🔴 已解除。
- [ ] 关闭服务器 `PasswordAuthentication`（先确认密钥可控 + 保留 Web 控制台）。
- [ ] **澄清并补全 `106` 数据层归属**：向量(pgvector)在 106、图谱(AGE)待常驻；Redis 主从已跨 137/106 建立（原报告仅描述 137，遗漏 106）。
- [ ] `age` 容器重新常驻并初始化图谱，满足 P0 DoD 的"AGE 容器完成图谱初始化"。
- [ ] 确认 `agent-backend` 是否并入 mock，还是需独立部署。
- [ ] 是否清理 `optimistic_payne`（hello-world 测试容器）。
- [ ] 是否将本报告回填进设计文档，并标注"当前部署为 mock 联调阶段"。

---

*附：本报告所有"现状"数据均来自 2026-10-21 对 `124.221.253.137` 的实测；"计划"数据来自 `tes-edu/docker-compose.yml` 与 `docx/` 设计文档。*
