"""Web 管理后台服务（B 端运营中枢）。

提供教研内容管理、学情看板、错题本、自动组卷、教师批改、家长周报、合规审核等接口。
所有路由挂在 /api/admin 下（由 Go 网关剥离前缀后转发到此服务）。

启动期：建表（幂等）+ 拉起 Kafka 入库消费端（未配置 Kafka 则走同步直写）。
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    analytics,
    auth,
    compliance,
    exam,
    grading,
    ingest,
    knowledge,
    learning,
    questions,
    reports,
    wrong_questions,
)
from app.core.config import settings
from app.core.db import get_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("web-backend")

_worker_tasks: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) 建表幂等；DB 未就绪时不阻断服务启动
    try:
        from app.services import course_kb as svc

        await svc.init_course_kb()
        logger.info("course_kb 表初始化完成")
    except Exception as e:  # noqa: BLE001
        logger.warning("course_kb 初始化跳过：%s", e)

    # 2) Kafka 消费端：题库入库 + 学情事件；未配置则入库接口自动降级为同步直写
    if settings.kafka_enabled:
        from app.workers.ingest_worker import run as run_ingest
        from app.workers.student_event_worker import run as run_events

        _worker_tasks = [
            asyncio.create_task(run_ingest()),
            asyncio.create_task(run_events()),
        ]
        logger.info(
            "Kafka 消费端已启动：%s / %s",
            settings.kafka_topic_ingest,
            settings.kafka_topic_event,
        )
    else:
        logger.info("Kafka 未配置，入库走同步直写")

    yield

    # ---- 关闭 ----
    for t in _worker_tasks:
        t.cancel()
    for t in _worker_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    try:
        from app.core.kafka_client import stop_producer

        await stop_producer()
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.core.db import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Tes Edu Web Backend", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "db_configured": bool(settings.database_url),
        "kafka_enabled": settings.kafka_enabled,
        "tables": [
            "course_kb",
            "kb_knowledge_points",
            "wrong_questions",
            "submissions",
            "compliance_items",
        ],
    }


@app.get("/metricsz")
async def metricsz(
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
):
    """匿名归因质量指标（S5 ⑤）：无任何 student_id / 题干等个人信息。

    deterministic_ratio 是"确定性归因占比"——查表命中的比例，
    即不依赖模型、结果可复现的判分占比，是可对外讲的质量数字。
    """
    payload = {"window_days": days, "generated_at": datetime.now(timezone.utc).isoformat()}
    try:
        from app.services import attribution, learning

        rows = await learning.fetch_attribution_metrics(db, days=days)
        payload.update(attribution.metrics_payload(rows))
        payload["status"] = "ok"
    except Exception as e:  # noqa: BLE001 指标接口不应因 DB 未就绪而 500
        payload.update({"status": "degraded", "error": str(e)})
    return payload


# 统一前缀 /api/admin（网关已剥离此前缀再转发，这里重新挂上以便本地直连也一致）
app.include_router(auth.router, prefix="/api/admin")
app.include_router(knowledge.router, prefix="/api/admin")
app.include_router(learning.router, prefix="/api/admin")
app.include_router(analytics.router, prefix="/api/admin")
app.include_router(wrong_questions.router, prefix="/api/admin")
app.include_router(exam.router, prefix="/api/admin")
app.include_router(grading.router, prefix="/api/admin")
app.include_router(reports.router, prefix="/api/admin")
app.include_router(compliance.router, prefix="/api/admin")
app.include_router(questions.router, prefix="/api/admin")
app.include_router(ingest.router, prefix="/api/admin")


if __name__ == "__main__":
    import uvicorn

    # 端口与网关上游一致（http://web-backend:8000）
    uvicorn.run(app, host="0.0.0.0", port=8000)
