"""course_kb 数据访问层：建表、幂等 upsert、查询。

建表幂等，可在服务启动时调用；upsert 基于 content_hash 唯一约束去重。
"""
import csv
import io
import json
from typing import List, Optional

import openpyxl
from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import engine
from app.models.course_kb import Base, CourseQuestion
from app.models import ops as _ops  # noqa: F401  注册运营实体表，随 create_all 一并建出


async def init_course_kb() -> None:
    async with engine.begin() as conn:
        # pgvector 扩展仅 PostgreSQL 支持；sqlite（本地单测）跳过，避免语法错误。
        if engine.dialect.name == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def upsert_questions(rows: List[dict], db) -> dict:
    inserted = 0
    duplicated = 0
    failed = 0
    errors: List[dict] = []
    for idx, row in enumerate(rows):
        try:
            stmt = pg_insert(CourseQuestion).values(**row)
            stmt = stmt.on_conflict_do_nothing(index_elements=["content_hash"])
            result = await db.execute(stmt)
            if result.rowcount and result.rowcount > 0:
                inserted += 1
            else:
                duplicated += 1
        except Exception as e:  # 单行失败不影响其余
            failed += 1
            errors.append({"index": idx, "reason": str(e)})
    await db.commit()
    return {"inserted": inserted, "duplicated": duplicated, "failed": failed, "errors": errors}


async def search_questions(
    db,
    q: Optional[str] = None,
    subject: Optional[str] = None,
    qtype: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    limit: int = 50,
) -> List[CourseQuestion]:
    stmt = select(CourseQuestion)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(CourseQuestion.stem.ilike(like), CourseQuestion.analysis.ilike(like))
        )
    if subject:
        stmt = stmt.where(CourseQuestion.subject == subject)
    if qtype:
        stmt = stmt.where(CourseQuestion.type == qtype)
    if knowledge_point:
        # JSONB 包含查询（知识点标签）
        stmt = stmt.where(CourseQuestion.knowledge_points.contains([knowledge_point]))
    stmt = stmt.limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def list_questions(db, limit: int = 50) -> List[CourseQuestion]:
    res = await db.execute(select(CourseQuestion).limit(limit))
    return list(res.scalars().all())


def parse_file(content: bytes, filename: str) -> List[dict]:
    """Excel/JSON/CSV → 原始 dict 列表（未清洗）。"""
    name = (filename or "").lower()
    if name.endswith(".json"):
        data = json.loads(content.decode("utf-8"))
        if isinstance(data, dict):
            return data.get("questions", [data])
        return data
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        return [dict(r) for r in reader]
    if name.endswith((".xlsx", ".xls")):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        rows = list(ws.values)
        if not rows:
            return []
        header = [str(h).strip() for h in rows[0]]
        return [dict(zip(header, r)) for r in rows[1:]]
    raise ValueError("不支持的文件类型，仅支持 .json/.csv/.xlsx")
