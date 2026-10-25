"""题库入库接口（教研产出 → course_kb）。

链路：教研员上传 Excel/JSON/CSV → parser 清洗（字段归一 + content_hash 去重指纹）
→ 投递 Kafka（course_kb_ingest）→ worker 消费落库。

Kafka 未配置时自动降级为同步直写，保证功能始终可用。
可用 ?sync=true 强制同步写（便于验证/小批量即时入库）。
"""
from typing import List

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.kafka_client import publish_ingest
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import QuestionIn
from app.services import course_kb as svc

# parse_file 定义在 app.services.course_kb（原从 parser 导入会 ImportError）
from app.services.parser import parse_records

router = APIRouter(prefix="/ingest", tags=["ingest"])


async def _dispatch(rows: List[dict], db: AsyncSession, force_sync: bool = False) -> dict:
    """异步投递 or 同步直写。"""
    if settings.kafka_enabled and not force_sync:
        await publish_ingest(rows)
        return {"mode": "async", "queued": len(rows), "topic": settings.kafka_topic_ingest}
    report = await svc.upsert_questions(rows, db)
    return {"mode": "sync", **report}


@router.post("/questions/json", summary="批量导入（JSON 数组）")
async def import_json(
    payload: List[QuestionIn],
    sync: bool = Query(False, description="强制同步写库（跳过 Kafka）"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    # 过 parser 统一字段并生成 content_hash（去重指纹）
    rows = parse_records([p.model_dump() for p in payload])
    result = await _dispatch(rows, db, force_sync=sync)
    return {"received": len(payload), "parsed": len(rows), **result}


@router.post("/upload", summary="上传文件导入（.json/.csv/.xlsx）")
async def import_upload(
    file: UploadFile = File(...),
    sync: bool = Query(False, description="强制同步写库（跳过 Kafka）"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    raw = svc.parse_file(content, file.filename or "")
    rows = parse_records(raw)
    result = await _dispatch(rows, db, force_sync=sync)
    return {"filename": file.filename, "received": len(raw), "parsed": len(rows), **result}
