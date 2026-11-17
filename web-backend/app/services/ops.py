"""B 端运营实体的真实数据访问层（替换原 API 层的内存 _STORE 占位）。

S5 追加：错题本 enrich —— 按 (content_hash, wrong_step_no) 取步骤的 correction，
并取知识点卡的 understanding/memory，让错题本从"记一道错题"升级为"知道错在第几步、怎么改"。
"""
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course_kb import CourseQuestion
from app.models.ops import (
    ComplianceItem,
    CourseKbAnnotationSuggestion,
    CourseKbStep,
    KnowledgePoint,
    Submission,
    WrongQuestion,
)
from app.services import attribution


# ---------------- 知识点 ----------------
async def list_knowledge(
    db: AsyncSession,
    subject: Optional[str] = None,
    grade: Optional[str] = None,
    limit: int = 100,
) -> List[KnowledgePoint]:
    stmt = select(KnowledgePoint)
    if subject:
        stmt = stmt.where(KnowledgePoint.subject == subject)
    if grade:
        stmt = stmt.where(KnowledgePoint.grade == grade)
    res = await db.execute(stmt.limit(limit))
    return list(res.scalars().all())


async def create_knowledge(db: AsyncSession, payload: dict) -> KnowledgePoint:
    obj = KnowledgePoint(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 错题本 ----------------
async def list_wrong(
    db: AsyncSession,
    student_id: str,
    limit: int = 100,
    tenant_id: Optional[str] = None,
    tenant_ids: Optional[List[str]] = None,
) -> List[WrongQuestion]:
    """后台错题列表（迁移 0023 起带租户隔离）。

    - ``tenant_ids`` 非空：区域管理员口径，返回本区域全部租户（IN）。
    - ``tenant_id`` 非空：单租户（其余角色）。
    - 两者都空：平台级跨租户只读（admin）——**必须不加 where**，写成
      ``col == None`` 会被 SQLAlchemy 编译成 IS NULL，静默查空。
    """
    stmt = select(WrongQuestion).where(
        WrongQuestion.student_id == student_id,
        WrongQuestion.deleted_at.is_(None),
    )
    if tenant_ids:
        stmt = stmt.where(WrongQuestion.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(WrongQuestion.tenant_id == tenant_id)
    res = await db.execute(stmt.limit(limit))
    return list(res.scalars().all())


async def create_wrong(db: AsyncSession, payload: dict) -> WrongQuestion:
    obj = WrongQuestion(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 错题本 enrich（S5 ④）----------------
async def fetch_steps(
    db: AsyncSession, pairs: Sequence[Tuple[str, int]]
) -> Dict[Tuple[str, int], CourseKbStep]:
    """按 (content_hash, step_no) 批量取步骤（含归因映射，供 correction 抽取）。"""
    pairs = [p for p in (pairs or []) if p and p[0]]
    if not pairs:
        return {}
    stmt = select(CourseKbStep).where(
        tuple_(CourseKbStep.content_hash, CourseKbStep.step_no).in_(pairs)
    )
    res = await db.execute(stmt)
    return {(s.content_hash, s.step_no): s for s in res.scalars().all()}


async def fetch_kp_cards(db: AsyncSession, names: Sequence[str]) -> Dict[str, KnowledgePoint]:
    """按知识点名取知识点卡（understanding/memory）。"""
    names = [n for n in (names or []) if n]
    if not names:
        return {}
    res = await db.execute(
        select(KnowledgePoint).where(KnowledgePoint.title.in_(names))
    )
    return {k.title: k for k in res.scalars().all() if k.title}


def _parse_options(raw) -> Optional[List[dict]]:
    """options 列是 Text（存 JSON 字符串），schema 却要求 List[dict]。

    历史数据可能是空串、非法 JSON 或已经被上游解析好的 list；任何异常都退成
    None，绝不能让一条脏数据把整个错题列表拖成 500。
    """
    if not raw:
        return None
    if isinstance(raw, (list, tuple)):
        return [o for o in raw if isinstance(o, dict)]
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return [o for o in val if isinstance(o, dict)] if isinstance(val, list) else None


async def enrich_wrong(db: AsyncSession, rows: Sequence[WrongQuestion]) -> List[dict]:
    """错题本列表增强：原字段 + 错因中文名 + 该步正确做法 + 知识点卡。

    取不到步骤（历史数据无 content_hash）时原字段照常返回，增强字段为 None，
    不因为"查不到"就让整个列表报错。
    """
    rows = list(rows or [])
    steps: Dict[Tuple[str, int], CourseKbStep] = {}
    cards: Dict[str, KnowledgePoint] = {}
    if rows:
        pairs = {(w.content_hash, w.wrong_step_no or 0) for w in rows if w.content_hash}
        try:
            steps = await fetch_steps(db, pairs)
        except Exception:  # noqa: BLE001 步骤只是增强信息
            steps = {}
        if steps:
            try:
                cards = await fetch_kp_cards(
                    db, [s.kp_name for s in steps.values() if s.kp_name]
                )
            except Exception:  # noqa: BLE001
                cards = {}

    out: List[dict] = []
    for w in rows:
        item = {
            "id": w.id,
            "student_id": w.student_id,
            "tenant_id": w.tenant_id,
            "question": w.question,
            "wrong_answer": w.wrong_answer or "",
            "correct_answer": w.correct_answer or "",
            "knowledge_point": w.knowledge_point or "",
            "created_at": w.created_at.isoformat() if w.created_at else None,
            # 2026-11-14：题型 / 选项原本没进 dict，pydantic 静默填默认值，
            # 后台列表里这两列恒 None，拿不到题源既有的题型信息。
            "question_type": w.question_type,
            "options": _parse_options(w.options),
            "content_hash": w.content_hash,
            "wrong_step_no": w.wrong_step_no,
            "error_type": w.error_type,
            "evidence": w.evidence,
            "attribution_source": w.attribution_source,
            "source": w.source,
        }
        step = steps.get((w.content_hash, w.wrong_step_no or 0))
        card = cards.get(step.kp_name) if (step is not None and step.kp_name) else None
        item.update(attribution.build_wrong_question_enrich(step, w.error_type, card))
        out.append(item)
    return out


# ---------------- 批改 ----------------
async def create_submission(db: AsyncSession, payload: dict) -> Submission:
    obj = Submission(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def get_submission(db: AsyncSession, submission_id: int) -> Optional[Submission]:
    return await db.get(Submission, submission_id)


async def review_submission(
    db: AsyncSession, obj: Submission, score: float, comment: str
) -> Submission:
    obj.score = score
    obj.comment = comment
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- 合规审核 ----------------
async def list_compliance(
    db: AsyncSession,
    status: Optional[str] = None,
    tenant_id: Optional[str] = None,
    tenant_ids: Optional[List[str]] = None,
    limit: int = 100,
) -> List[ComplianceItem]:
    """待审列表。

    - ``tenant_ids`` 非空：区域管理员口径，返回本区域全部租户（IN）。
    - ``tenant_id`` 非空：单租户（其余角色）。
    - 两者都空：平台级跨租户只读（admin）——**必须是不加 where**，
      绝不能写成 ``column == None``（会编译成 IS NULL，一条都查不到）。
    """
    stmt = select(ComplianceItem)
    if tenant_ids:
        stmt = stmt.where(ComplianceItem.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(ComplianceItem.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(ComplianceItem.status == status)
    stmt = stmt.order_by(
        ComplianceItem.created_at.desc().nullslast(),
        ComplianceItem.id.desc(),
    ).limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def create_compliance(db: AsyncSession, payload: dict) -> ComplianceItem:
    obj = ComplianceItem(**payload)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def get_compliance(db: AsyncSession, item_id: int) -> Optional[ComplianceItem]:
    return await db.get(ComplianceItem, item_id)


async def review_compliance(
    db: AsyncSession, obj: ComplianceItem, status: str, actor_id: Optional[str] = None
) -> ComplianceItem:
    """审核结论 + 留痕。谁、何时判的必须落库，否则结论不可追溯。"""
    obj.status = status
    obj.reviewed_by = actor_id
    obj.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(obj)
    return obj


# ---------------- T-CAP1 标注审核（标注→审核→入库闭环） ----------------
#: 可写回 course_kb 的字段白名单，杜绝任意字段写入（建议表 field_name 理论上可塞任何值）。
_ANNOTATION_WRITABLE = {"difficulty": "difficulty", "year": "year", "paper_type": "paper_type"}


def coerce_annotation_value(field_name: str, suggested_value: str):
    """approve 前二次校验并转型，返回可写库的值；不合法返回 (None, reason)。

    与生成脚本 coerce_value 口径一致：difficulty 1~5、year 1900~2100、paper_type 非空串。
    这是写回前最后一道闸——即使建议表被污染，非法值也进不了 course_kb。
    """
    if field_name not in _ANNOTATION_WRITABLE:
        return None, f"字段不在白名单：{field_name}"
    raw = (suggested_value or "").strip()
    if not raw or raw.lower() == "null":
        return None, "建议值为空"
    if field_name == "difficulty":
        try:
            n = int(raw)
        except ValueError:
            return None, "difficulty 非整数"
        if not 1 <= n <= 5:
            return None, "difficulty 超出 1~5"
        return n, None
    if field_name == "year":
        try:
            n = int(raw)
        except ValueError:
            return None, "year 非整数"
        if not 1900 <= n <= 2100:
            return None, "year 超出 1900~2100"
        return n, None
    return raw, None  # paper_type 字符串


async def list_annotation_suggestions(
    db: AsyncSession,
    status: Optional[str] = None,
    field_name: Optional[str] = None,
    limit: int = 100,
) -> List[CourseKbAnnotationSuggestion]:
    """待校队列（可按状态/字段过滤），新建议优先。

    题干不在建议表里（建议表只存 content_hash，见迁移 0024），这里按 hash
    一次 IN 批量回填 ``question_stem`` 非映射属性供审核页展示：N 条建议只多
    一次查询，不产生 N+1；关联不到（题目已删/归档）时该属性为 None，
    前端回退显示哈希。该属性不落库、不参与后续 UPDATE。
    """
    stmt = select(CourseKbAnnotationSuggestion)
    if status:
        stmt = stmt.where(CourseKbAnnotationSuggestion.status == status)
    if field_name:
        stmt = stmt.where(CourseKbAnnotationSuggestion.field_name == field_name)
    stmt = stmt.order_by(
        CourseKbAnnotationSuggestion.created_at.desc().nullslast(),
        CourseKbAnnotationSuggestion.id.desc(),
    ).limit(limit)
    res = await db.execute(stmt)
    items = list(res.scalars().all())

    hashes = {s.content_hash for s in items if s.content_hash}
    if not hashes:
        return items
    stem_rows = await db.execute(
        select(CourseQuestion.content_hash, CourseQuestion.stem).where(
            CourseQuestion.content_hash.in_(hashes)
        )
    )
    stems = {h: stem for h, stem in stem_rows.all()}
    for s in items:
        s.question_stem = stems.get(s.content_hash)
    return items


async def get_annotation(db: AsyncSession, item_id: int) -> Optional[CourseKbAnnotationSuggestion]:
    return await db.get(CourseKbAnnotationSuggestion, item_id)


async def review_annotation(
    db: AsyncSession,
    obj: CourseKbAnnotationSuggestion,
    decision: str,
    actor_id: Optional[str] = None,
) -> dict:
    """审核一条建议。

    approve：二次校验 → 写回 course_kb 对应字段 → 标记 approved + 留痕。
    reject：只标记 rejected + 留痕，不碰 course_kb。

    返回 ``{"ok": bool, "status": ..., "reason": ...}``；非法值返回 ok=False，
    不写回也不改状态（仍 pending），由调用方转 400。
    """
    status = decision.lower()
    if status not in ("approved", "rejected"):
        raise ValueError(f"未知审核结论：{decision}")
    if obj.status != "pending":
        return {"ok": False, "reason": f"该建议已处理（{obj.status}），不可重复审核",
                "status": obj.status}
    if status == "approved":
        value, err = coerce_annotation_value(obj.field_name, obj.suggested_value)
        if err:
            return {"ok": False, "reason": f"建议值非法，拒绝写回：{err}", "status": obj.status}
        col = _ANNOTATION_WRITABLE[obj.field_name]
        res = await db.execute(
            select(CourseQuestion).where(CourseQuestion.content_hash == obj.content_hash)
        )
        question = res.scalar_one_or_none()
        if question is None:
            return {"ok": False, "reason": "对应题目不存在，拒绝写回", "status": obj.status}
        setattr(question, col, value)
        # 注意：不 commit 题与建议之间的原子性——同一 session，一次 commit，保持一致
    obj.status = status
    obj.reviewed_by = actor_id
    obj.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(obj)
    return {"ok": True, "status": status, "reason": None}


async def annotation_stats(db: AsyncSession) -> dict:
    """产能看板：待校队列按字段/状态分布 + course_kb 标注覆盖缺口。"""
    res = await db.execute(
        select(
            CourseKbAnnotationSuggestion.field_name,
            CourseKbAnnotationSuggestion.status,
            func.count(CourseKbAnnotationSuggestion.id),
        ).group_by(
            CourseKbAnnotationSuggestion.field_name,
            CourseKbAnnotationSuggestion.status,
        )
    )
    queue = {}
    for field_name, status, n in res.all():
        queue.setdefault(field_name, {})[status] = n

    gap = await db.execute(
        select(
            func.count().label("total"),
            func.count().filter(CourseQuestion.difficulty.is_(None) | (CourseQuestion.difficulty == 3)).label("difficulty_pending"),
            func.count().filter(CourseQuestion.year.is_(None)).label("year_pending"),
            func.count().filter(
                (CourseQuestion.paper_type.is_(None)) | (CourseQuestion.paper_type == "")
            ).label("paper_type_pending"),
        ).where(CourseQuestion.status == "published")
    )
    row = gap.one()
    return {
        "queue": queue,
        "course_kb": {
            "published_total": row.total,
            "difficulty_gap": row.difficulty_pending,
            "year_gap": row.year_pending,
            "paper_type_gap": row.paper_type_pending,
        },
    }
