# Tes Edu Agent 后端（Python / FastAPI）

系统"大脑"：接收网关转发的 C 端请求，编排 **RAG 检索 → LLM 解题 → 触类旁通**，并支持 OCR 拍照链路。

## 两种运行模式

| 模式 | 触发条件 | 行为 |
|---|---|---|
| **real** | 配置 `LLM_*` + `EMBED_*` + `DB_DSN` | 真实大模型（DeepSeek / 通义 / Qwen，OpenAI 兼容 `/v1`）+ 106 的 pgvector 向量检索 |
| **dev**（默认） | 缺密钥或未配 `DB_DSN` | 降级：本地确定性向量 + **基于真实检索上下文**拼装的讲解（不再是占位字符串），链路照样跑通 |

`/healthz` 会返回 `mode` 与各依赖就绪状态（`llm` / `embed` / `db` / `vlm`），便于线上自查当前跑在哪种模式。

## 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/app/solve` | 文本题：解题 + 讲解 + 触类旁通 |
| POST | `/api/app/solve_image` | 拍照题（multipart 图片，需配置视觉模型） |
| POST | `/api/app/similar` | 触类旁通变式题 |
| GET  | `/healthz` | 健康检查（含运行模式） |

> 网关会把 `/api/app/*` 前缀转发，本服务内部路由与之保持一致便于联调。

## 目录

```
agent-backend/
├── main.py                     # FastAPI 入口（含启动预热/关闭）
├── app/
│   ├── api/solve.py            # 路由层
│   ├── core/
│   │   ├── orchestrator.py     # 意图路由 + 工具编排（RAG→LLM→变式）
│   │   ├── config.py           # 环境变量与就绪判定
│   │   ├── embeddings.py       # 向量化（真实接口 / 本地回退）
│   │   ├── rag.py              # 检索（pgvector / 内存回退）
│   │   ├── llm.py              # 大模型解答（真实调用 / 有据回退）
│   │   └── ocr.py              # 拍照识题（视觉模型 / 降级）
│   ├── db/pg.py                # asyncpg 连接池
│   └── models/schemas.py       # Pydantic 模型
├── data/seed_questions.json    # 合成种子题库（人教A版高中数学主线）
├── sql/embeddings.sql          # 向量表结构（pgvector）
├── scripts/seed.py             # 灌库脚本
├── scripts/smoke.py            # 链路冒烟脚本
├── scripts/eval_retrieval.py   # 检索质量抽测（对应 P1 DoD 召回准确率 ≥85%）
├── .env.example
├── requirements.txt
└── Dockerfile
```

## 快速开始

**dev 模式（零密钥，立即可跑通）**
```bash
uvicorn main:app --reload --port 8000
python scripts/smoke.py        # 验证 检索→讲解→变式 三段链路
python scripts/eval_retrieval.py  # 检索召回质量抽测（top1 知识点命中率）
```

**real 模式（真实大模型 + pgvector）**
```bash
cp .env.example .env           # 填 LLM_* / EMBED_* / DB_DSN
psql "$DB_DSN" -f sql/embeddings.sql     # 建表与向量索引
python scripts/seed.py                    # 灌种子题库并生成向量
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 环境变量

见 `.env.example`。要点：
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`：三家均走 OpenAI 兼容接口，换供应商只改这三个值。
- `EMBED_MODEL` **留空则走本地向量回退**（top1 相关度仅 ~0.5）；填上才启用真实 embedding。已验证可用的千问向量模型：`qwen3.7-text-embedding`（推荐）、`text-embedding-v4`、`qwen3.7-text-embedding-flash`，均为 1024 维。**并非每个 MaaS 端点都挂载了 embedding 模型**（有实测返回 `Model not exist` 的情况），不通就换端点或自建 Qwen3-Embedding。
- `EMBED_DIM` 必须与 `sql/embeddings.sql` 里的 `VECTOR(n)` 一致（默认 1024）。
- `DB_DSN` 指向 106 的 `pgvector/pg16`；不配则自动走内存库。

## Docker

```bash
docker build -t tes-edu-agent . && docker run -p 8000:8000 --env-file .env tes-edu-agent
```

> `.dockerignore` 已排除 `.env`——**密钥永不进镜像**，运行时由 `env_file` 或环境变量注入。

## 部署到 137（替换原 mock 桩）

137 的编排在 `/opt/tes-edu-gateway/docker-compose.yml`（项目名 `tes-edu-gateway`），
`agent-backend` 服务原用 `edu-mock-backend` 镜像，已替换为本地构建的真实镜像：

```bash
# 1) 源码传到 137 并构建（.env 不随源码分发）
docker build -t edu-agent-backend:real /opt/tes-edu-agent

# 2) compose 中该服务改为 image: edu-agent-backend:real，并挂 env_file
#    （环境变量文件 /opt/tes-edu-gateway/agent-backend.env，权限 600）

# 3) 只重建这一个服务（web-backend 仍是 mock，不动）
docker compose -p tes-edu-gateway up -d agent-backend

# 4) 验证
docker exec edu-agent-backend python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read())"
# 期望: {"status":"ok","mode":"real","llm":true,"embed":true,"db":true,...}
```

**回滚**：改回 `image: registry.cn-shanghai.aliyuncs.com/mis_ser/edu-mock-backend:latest`
后重新 `up -d`；修改前的 compose 备份为 `docker-compose.yml.bak.<时间戳>`。

注意：`/api/app/*` 经网关需鉴权，直接 curl 会返回 `{"error":"missing token"}`，
需用有效 JWT 或从 App 侧验证。
