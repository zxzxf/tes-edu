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
from app.core.intake_guard import text_fingerprint
from app.models.course_kb import Base, CourseQuestion
from app.models import ops as _ops  # noqa: F401  注册运营实体表，随 create_all 一并建出
from app.services import kb_guard


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
    inserted_hashes: List[str] = []
    for idx, row in enumerate(rows):
        try:
            # pg_insert 是 Core 语句，不走 ORM 事件；这里显式补 text_fp，
            # 否则 Excel/JSON 导入的新题在库里没有指纹、永远躲过第一闸。
            row = dict(row)
            if row.get("stem") and not row.get("text_fp"):
                row["text_fp"] = text_fingerprint(row["stem"])
            stmt = pg_insert(CourseQuestion).values(**row)
            stmt = stmt.on_conflict_do_nothing(index_elements=["content_hash"])
            result = await db.execute(stmt)
            if result.rowcount and result.rowcount > 0:
                inserted += 1
                if row.get("content_hash"):
                    inserted_hashes.append(str(row["content_hash"]))
            else:
                duplicated += 1
        except Exception as e:  # 单行失败不影响其余
            failed += 1
            errors.append({"index": idx, "reason": str(e)})

    # T-CAP4 入库即校验：批量导入（Excel/JSON/CSV，同步与 Kafka 消费都汇聚到这里）
    # 同样过三闸。明确错误转 quarantine；「无法判定」按人工链路口径不打标。
    guard = kb_guard.empty_stats(
        kb_guard.chain_enabled(kb_guard.CHAIN_IMPORT), len(inserted_hashes),
        kb_guard.CHAIN_IMPORT,
    )
    if inserted_hashes and kb_guard.chain_enabled(kb_guard.CHAIN_IMPORT):
        created = list((await db.execute(
            select(CourseQuestion).where(
                CourseQuestion.content_hash.in_(inserted_hashes)
            )
        )).scalars().all())
        guard = await kb_guard.apply_entry_guard(
            db, created,
            base_tag=kb_guard.base_tag_of(created, "import"),
            chain=kb_guard.CHAIN_IMPORT,
            scope_tenant_id=kb_guard.rows_tenant_scope(created),  # 本校去重
            tag_manual=False,
        )

    await db.commit()
    return {
        "inserted": inserted, "duplicated": duplicated,
        "failed": failed, "errors": errors, "guard": guard,
    }


async def search_questions(
    db,
    q: Optional[str] = None,
    subject: Optional[str] = None,
    qtype: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    grade: Optional[str] = None,
    difficulty_min: Optional[int] = None,
    difficulty_max: Optional[int] = None,
    status: Optional[str] = None,
    answerable: bool = False,
    limit: int = 50,
    tenant_id: Optional[str] = None,
    tenant_ids: Optional[list] = None,
) -> List[CourseQuestion]:
    """按条件检索题目。

    ``tenant_id`` 非空时**只返回本租户题目**（M0-4 隔离验收）：教师端检索
    绝不能捞到别校的题，包括别校草稿。``tenant_ids`` 非空时按区域管理员口径
    返回本区域全部租户（IN）。两者都为 None 表示平台级跨租户只读，供运维/脚本调用。

    M4-1 手动出卷的筛选器要按 年级/难度/题型/学科/kp 挑题，且只能挑
    ``published`` + 非隔离 + 有答案的题（``answerable=True``）；这些条件
    放在 SQL 里，避免把整库拉回内存再过滤。
    """
    stmt = select(CourseQuestion)
    if tenant_ids:
        stmt = stmt.where(CourseQuestion.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(CourseQuestion.tenant_id == tenant_id)
    if grade:
        stmt = stmt.where(CourseQuestion.grade == grade)
    if difficulty_min is not None:
        stmt = stmt.where(CourseQuestion.difficulty >= int(difficulty_min))
    if difficulty_max is not None:
        stmt = stmt.where(CourseQuestion.difficulty <= int(difficulty_max))
    if status:
        stmt = stmt.where(CourseQuestion.status == status)
    if answerable:
        stmt = stmt.where(CourseQuestion.answer.isnot(None))
        stmt = stmt.where(
            (CourseQuestion.source_tag.is_(None))
            | (CourseQuestion.source_tag != "quarantine")
        )
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
    stmt = stmt.order_by(CourseQuestion.id.desc()).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def list_questions(
    db, limit: int = 50, tenant_id: Optional[str] = None, tenant_ids: Optional[list] = None
) -> List[CourseQuestion]:
    """最近录入优先：录题页要在首屏看到刚进来的题，按 id 倒序。

    ``tenant_id`` 非空时只返回本租户题目（M0-4 隔离验收）；``tenant_ids`` 非空时
    按区域管理员口径返回本区域全部租户（IN）；都为 None 表示平台级跨租户只读。

    存量导入行 id 小、created_at 可能为 NULL，用 id 排序比时间更稳。
    """
    stmt = select(CourseQuestion)
    if tenant_ids:
        stmt = stmt.where(CourseQuestion.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(CourseQuestion.tenant_id == tenant_id)
    stmt = stmt.order_by(CourseQuestion.id.desc()).limit(limit)
    res = await db.execute(stmt)
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
