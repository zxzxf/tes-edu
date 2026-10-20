# Tes Edu Agent 后端（Python / FastAPI）— P0

系统"大脑"：接收网关转发的 C 端请求，编排 **RAG 检索 → LLM 解题 → 触类旁通**，并预留 OCR 拍照链路。

## 路由
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/app/solve` | 文本题：解题 + 讲解 + 触类旁通 |
| POST | `/api/app/solve_image` | 拍照题（multipart 图片） |
| POST | `/api/app/similar` | 触类旁通变式题 |
| GET  | `/healthz` | 健康检查 |

> 注意：网关会把 `/api/app/*` 前缀剥离后转发，因此本服务内部路由就是 `/api/app/solve` 等（与网关保持一致便于联调）。

## 目录
```
agent-backend/
├── main.py                 # FastAPI 入口
├── app/
│   ├── api/solve.py        # 路由层
│   ├── core/
│   │   ├── orchestrator.py # 意图路由 + 工具编排
│   │   ├── llm.py          # LLM 解答引擎（占位）
│   │   ├── rag.py          # 向量检索（占位）
│   │   └── ocr.py          # 图片识别（占位）
│   └── models/schemas.py   # Pydantic 模型
├── requirements.txt
└── Dockerfile
```

## 运行
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# 或
docker build -t tes-edu-agent . && docker run -p 8000:8000 tes-edu-agent
```

P0 阶段所有 LLM/RAG/OCR 均为**占位返回**，便于前后端先跑通链路；P1 起逐步接入真实引擎与向量库（pgvector）。
