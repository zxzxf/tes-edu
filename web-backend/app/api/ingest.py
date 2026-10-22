"""题库入库接口（教研产出 → course_kb）。

教研员/管理员上传 Excel/JSON/CSV，经 parser 清洗后幂等 upsert。
入库前建议过 compliance 审核（计划书要求）；此处仅做最小链路，审核 gate 后续接入。
"""
from typing import List

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import QuestionIn
from app.services import course_kb as svc
from app.services.parser import parse_file, parse_records

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/questions/json", summary="批量导入（JSON 数组）")
async def import_json(
    payload: List[QuestionIn],
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
    db: AsyncSession = Depends(get_db),
):
    rows = [p.model_dump() for p in payload]
    report = await svc.upsert_questions(rows, db)
    return {"received": len(rows), **report}


@router.post("/upload", summary="上传文件导入（.json/.csv/.xlsx）")
async def import_upload(
    file: UploadFile = File(...),
    user=Depends(require_role(Role.ADMIN, Role.RESEARCHER)),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    raw = svc.parse_file(content, file.filename or "")
    rows = parse_records(raw)
    report = await svc.upsert_questions(rows, db)
    return {"filename": file.filename, "received": len(raw), "parsed": len(rows), **report}
