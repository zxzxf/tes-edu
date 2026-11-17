"""T-CAP4 异常队列 API（B 端）：让三闸打标/隔离的题有专门界面处置。

背景：v5.20 三条入库链路（采集 / 教师录题 / 批量导入）已统一走三闸。被挡下的题
此前只能靠 ``GET /exam/questions?status=quarantine`` 看到一条 status，**看不到
为什么被挡**（gate/reason 只活在入库进程的 stats 里）。迁移 0025 把 gate/reason
落库后，本模块提供三件事：

- ``GET /stats``   队列总览：隔离数 / 待人工数 / 已归档数 + 按闸、按来源链路分布；
- ``GET /items``   队列明细：``kind=quarantine`` 隔离待处置、``kind=flagged`` 仅打标
                   待人工确认、``kind=archived`` 已归档；重复题附带 ``dup_of``
                   指向它和哪一题重复（人工判断该清哪条的依据）；
- ``POST /{id}/resolve``  三种处置：
    * ``release``  确认不是脏题 → ``draft``（回到常规审核流程）；
    * ``publish``  确认干净且答案合规 → 直接 ``published``（答案不合规 409）；
    * ``archive``  确认重复/脏题 → 保留数据但移出队列（不做硬删除，删数据不可逆）。

**为什么不提供硬删除**：异常队列里的「重复」是靠文本指纹判的，指纹相同不等于
教研口径上该删哪一条（可能两条都不完整、需要合并）。批量删数据不可逆，留给
单独的数据治理脚本 + 人工确认。

权限与标注页同口径（admin / region_admin / teacher）；school 不授——与审核 API
一致，避免造出「看得见点不动」的死链。租户隔离：读侧走 ``resolve_read_scope``，
处置时把同一 scope 下推进查询，范围外的题直接 404（admin 全量，其余按本租户/
本区域）。
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

# 复用审核接口的答案合规口径：否则会出现「异常队列页能发布、审核页 409 拒绝」的分裂。
from app.api.exam import _answer_acceptable
from app.core.db import get_db
from app.core.intake_guard import TAG_ARCHIVED, TAG_MANUAL, TAG_RELEASED, text_fingerprint
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import CourseQuestion
from app.services.scope import resolve_read_scope, where_tenant

router = APIRouter(prefix="/quarantine", tags=["tcap4-quarantine"])

_ROLES = (Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)

_SUFFIX_QUARANTINE = f":{TAG_MANUAL}"      # 打标待人工（manual_review）
_SUFFIX_ARCHIVED = f":{TAG_ARCHIVED}"      # 已归档，移出待办

#: source_tag 前缀 → 链路名（异常队列按链路分组，便于定位是哪条入库口来的脏题）
_CHAIN_OF = {"auto_crawl": "crawl", "teacher": "teacher", "import": "import"}

#: gate → 中文名。与 ``app/core/intake_guard.py`` 各闸返回的 gate 值一一对应；
#: 未收录的新 gate 前端按原值兜底显示，不会丢信息。
GATE_LABEL = {
    "dedup_exact": "内容哈希重复",
    "dedup_text": "题干指纹重复",
    "missing_answer": "答案缺失",
    "answer_out_of_range": "选项越界",
    "mechanical_mismatch": "代入不自洽",
    "dual_source_conflict": "双源答案冲突",
    "choice_no_second_source": "选择题无第二来源",
    "numeric_no_reference": "无参照答案可比对",
    "manual_only": "需人工判断",
}


def _base_of(tag: Optional[str]) -> str:
    """``teacher:guard_quarantine`` → ``teacher``（保留来源前缀写回时用）。"""
    base = str(tag or "").split(":", 1)[0].strip()
    return base or "unknown"


def _tag_missing_expr():
    """source_tag 为 NULL 也要算「未归档」——SQL 里 ``NOT LIKE`` 遇 NULL 会整行丢掉。"""
    return or_(
        CourseQuestion.source_tag.is_(None),
        ~CourseQuestion.source_tag.like(f"%{_SUFFIX_ARCHIVED}"),
    )


def _is_pending_quarantine(row: CourseQuestion) -> bool:
    return (
        row.status == "quarantine"
        and not str(row.source_tag or "").endswith(_SUFFIX_ARCHIVED)
    )


def _item_out(row: CourseQuestion, dup_of: Optional[int] = None) -> dict:
    return {
        "id": row.id,
        "subject": row.subject,
        "grade": row.grade,
        "type": row.type,
        "stem": row.stem,
        "options": row.options,
        "answer": row.answer,
        "analysis": row.analysis,
        "knowledge_points": row.knowledge_points or [],
        "difficulty": row.difficulty,
        "status": row.status,
        "source_tag": row.source_tag,
        "base_tag": _base_of(row.source_tag),
        "chain": _CHAIN_OF.get(_base_of(row.source_tag), "other"),
        "guard_gate": row.guard_gate,
        "guard_gate_label": GATE_LABEL.get(row.guard_gate or "", row.guard_gate),
        "guard_reason": row.guard_reason,
        "dup_of": dup_of,
        "tenant_id": row.tenant_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


async def _dup_index(db: AsyncSession) -> tuple[Dict[str, List[int]], Dict[str, List[int]]]:
    """全库 hash/指纹倒排索引，用于告诉人工「这道题和哪一题重复」。

    只在返回页里存在去重类 gate 时才建（多数请求不付这个成本）。题库上万后应
    改为持久化 ``text_fp`` 列 + 等值查询，已登记为后续优化项。
    """
    rows = (await db.execute(
        select(CourseQuestion.id, CourseQuestion.content_hash, CourseQuestion.stem)
    )).all()
    by_hash: Dict[str, List[int]] = {}
    by_fp: Dict[str, List[int]] = {}
    for qid, ch, stem in rows:
        if ch:
            by_hash.setdefault(str(ch), []).append(qid)
        fp = text_fingerprint(stem)
        if fp:
            by_fp.setdefault(fp, []).append(qid)
    return by_hash, by_fp


@router.get("/stats")
async def stats(
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """队列总览：一次聚合出隔离/待人工/已归档 + 按闸、按来源链路分布。"""
    scope = await resolve_read_scope(user, db)
    stmt = select(
        CourseQuestion.status,
        CourseQuestion.source_tag,
        CourseQuestion.guard_gate,
        func.count(),
    ).where(or_(
        CourseQuestion.status == "quarantine",
        CourseQuestion.source_tag.like(f"%{_SUFFIX_QUARANTINE}"),
        CourseQuestion.source_tag.like(f"%{_SUFFIX_ARCHIVED}"),
    ))
    stmt = where_tenant(stmt, CourseQuestion.tenant_id, scope)
    stmt = stmt.group_by(
        CourseQuestion.status, CourseQuestion.source_tag, CourseQuestion.guard_gate,
    )
    rows = (await db.execute(stmt)).all()

    quarantine = flagged = archived = 0
    by_gate: Dict[str, int] = {}
    by_chain: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for status, tag, gate, count in rows:
        tag = str(tag or "")
        base = _base_of(tag)
        if tag.endswith(_SUFFIX_ARCHIVED):
            archived += count
            continue
        if status == "quarantine":
            quarantine += count
        elif tag.endswith(_SUFFIX_QUARANTINE):
            flagged += count
        else:
            continue  # released / 正常来源，不计入待办
        key = gate or "unknown"
        by_gate[key] = by_gate.get(key, 0) + count
        chain = _CHAIN_OF.get(base, "other")
        by_chain[chain] = by_chain.get(chain, 0) + count
        by_source[tag] = by_source.get(tag, 0) + count

    return {
        "quarantine_total": quarantine,
        "flagged_total": flagged,
        "archived_total": archived,
        "by_gate": by_gate,
        "by_chain": by_chain,
        "by_source": by_source,
        "gate_labels": GATE_LABEL,
    }


@router.get("/items")
async def items(
    kind: str = Query("quarantine", pattern="^(quarantine|flagged|archived)$"),
    gate: Optional[str] = Query(None, description="按闸过滤（guard_gate 原值）"),
    q: Optional[str] = Query(None, description="题干关键词"),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """队列明细。``kind=quarantine`` 隔离待处置 / ``flagged`` 仅打标 / ``archived`` 已归档。"""
    scope = await resolve_read_scope(user, db)
    stmt = select(CourseQuestion)
    stmt = where_tenant(stmt, CourseQuestion.tenant_id, scope)

    if kind == "quarantine":
        stmt = stmt.where(CourseQuestion.status == "quarantine", _tag_missing_expr())
    elif kind == "flagged":
        stmt = stmt.where(CourseQuestion.source_tag.like(f"%{_SUFFIX_QUARANTINE}"))
    else:
        stmt = stmt.where(CourseQuestion.source_tag.like(f"%{_SUFFIX_ARCHIVED}"))

    if gate:
        stmt = stmt.where(CourseQuestion.guard_gate == gate)
    if q:
        stmt = stmt.where(CourseQuestion.stem.ilike(f"%{q.strip()}%"))

    rows = (await db.execute(
        stmt.order_by(CourseQuestion.id.desc()).limit(limit)
    )).scalars().all()

    by_hash: Dict[str, List[int]] = {}
    by_fp: Dict[str, List[int]] = {}
    if any(str(r.guard_gate or "").startswith("dedup_") for r in rows):
        by_hash, by_fp = await _dup_index(db)

    out = []
    for row in rows:
        dup_of = None
        if str(row.guard_gate or "") == "dedup_exact":
            others = [i for i in by_hash.get(str(row.content_hash or ""), []) if i != row.id]
            dup_of = others[0] if others else None
        elif str(row.guard_gate or "") == "dedup_text":
            fp = text_fingerprint(row.stem)
            others = [i for i in by_fp.get(fp or "", []) if i != row.id]
            dup_of = others[0] if others else None
        out.append(_item_out(row, dup_of))
    return out


class ResolveBody(BaseModel):
    action: str  # release / publish / archive


@router.post("/{question_id}/resolve")
async def resolve(
    question_id: int,
    body: ResolveBody,
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """处置一条异常题：放行入草稿 / 确认发布 / 归档移出队列。

    处置后清空 ``guard_gate``/``guard_reason``（避免「已处置但仍显示隔离原因」），
    留痕改由 ``source_tag`` 的 ``:guard_released``/``:guard_archived`` 后缀 +
    ``reviewed_by``/``reviewed_at`` 承载。
    """
    action = (body.action or "").strip()
    if action not in ("release", "publish", "archive"):
        raise HTTPException(status_code=422, detail="action 只支持 release / publish / archive")

    scope = await resolve_read_scope(user, db)
    stmt = where_tenant(
        select(CourseQuestion).where(CourseQuestion.id == question_id),
        CourseQuestion.tenant_id, scope,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="question not found")

    if not _is_pending_quarantine(row) and not str(row.source_tag or "").endswith(_SUFFIX_QUARANTINE):
        raise HTTPException(status_code=409, detail="该题不在异常队列（可能已处置）")

    base = _base_of(row.source_tag)
    if action == "release":
        row.status = "draft"
        row.source_tag = f"{base}:{TAG_RELEASED}"
    elif action == "publish":
        if not _answer_acceptable(row):
            raise HTTPException(status_code=409, detail="题目答案缺失或不合规，不能直接发布")
        row.status = "published"
        row.source_tag = f"{base}:{TAG_RELEASED}"
    else:  # archive：保留数据（不硬删），仅移出待办队列
        row.status = "quarantine"
        row.source_tag = f"{base}:{TAG_ARCHIVED}"

    row.guard_gate = None
    row.guard_reason = None
    row.reviewed_by = user.get("user_id")
    row.reviewed_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "id": row.id, "action": action, "status": row.status,
        "source_tag": row.source_tag,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }
