from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.solve import router as solve_router
from app.core import config, rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：dev 模式加载内存题库；真实模式建立 pg 连接池
    await rag.warm()
    yield
    # 关闭：先把待发的学情事件收尾，再停生产者、释放连接池
    try:
        from app.core import events

        await events.shutdown()
    except Exception:
        pass
    try:
        from app.db import pg

        await pg.close_pool()
    except Exception:
        pass


app = FastAPI(title="Tes Edu Agent Backend", version="0.2.0", lifespan=lifespan)

# 网关已做鉴权/限流；此处 CORS 仅便于本地联调，生产可收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(solve_router)


@app.get("/healthz")
async def healthz():
    # db=配置就绪；db_ping=真实连通性（T-A3 固化：读取侧探活，pgvector 挂了能立刻看见）
    db_ping: bool | None = None
    if config.db_ready():
        try:
            from app.db import pg

            pool = await pg.get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ping = True
        except Exception:
            db_ping = False
    return {
        "status": "ok",
        "mode": config.mode(),
        "llm": config.llm_ready(),
        "embed": config.embed_ready(),
        "db": config.db_ready(),
        "db_ping": db_ping,
        "vlm": config.vlm_ready(),
        "kafka": config.kafka_ready(),
    }
