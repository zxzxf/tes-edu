"""T-CAP4 异常队列 API（B 端）：让三闸打标/隔离的题有专门界面处置。

背景：v5.20 三条入库链路（采集 / 教师录题 / 批量导入）已统一走三闸。被挡下的题
此前只能靠 ``GET /exam/questions?status=quarantine`` 看到一条 status，**看不到
为什么被挡**（gate/reason 只活在入库进程的 stats 里）。迁移 0025 把 gate/reason
落库后，本模块提供三件事：

- ``GET /stats``   队列总览：隔离数 / 待人工数 / 已归档数 + 按闸、按来源链路分布；
- ``GET /items``   队列明细：``kind=quarantine`` 隔离待处置、``kind=flagged`` 仅打标
                   待人工确认、``kind=archived`` 已归档；重复题附带 ``dup_of``
                   指向它和哪一题重复（人工判断该清哪条的依据）；语义近似命中
                   （``dedup_semantic``）的 dup_of 来自迁移 0026 的落库列，
                   hash/指纹类仍用倒排索引现算；
- ``POST /{id}/resolve``  三种处置：
    * ``release``  确认不是脏题 → ``draft``（回到常规审核流程）；
    * ``publish``  确认干净且答案合规 → 直接 ``published``（答案不合规 409）；
    * ``archive``  确认重复/脏题 → 保留数据但移出队列（不做硬删除，删数据不可逆）；
- ``POST /batch-resolve``  一次处置多条，部分成功逐条回报，不让一条失败拖垮整批；
- ``GET /duplicate-groups`` / ``POST /duplicate-groups/{key}/resolve``
  存量 published 真题重复的**人工逐组裁决**：接口只摆出候选组，留哪条由人点选，
  ``keep_one`` 只归档组内其余项、保留项继续留在卷池，``keep_all`` 一条题都不动。

**为什么不提供硬删除**：异常队列里的「重复」是靠文本指纹判的，指纹相同不等于
教研口径上该删哪一条（可能两条都不完整、需要合并）。批量删数据不可逆，留给
单独的数据治理脚本 + 人工确认。

权限与标注页同口径（admin / region_admin / teacher）；school 不授——与审核 API
一致，避免造出「看得见点不动」的死链。租户隔离：读侧走 ``resolve_read_scope``，
处置时把同一 scope 下推进查询，范围外的题直接 404（admin 全量，其余按本租户/
本区域）。
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

# 复用审核接口的答案合规口径：否则会出现「异常队列页能发布、审核页 409 拒绝」的分裂。
from app.api.exam import _answer_acceptable
from app.core.db import get_db
from app.core.intake_guard import (
    TAG_ARCHIVED, TAG_MANUAL, TAG_RELEASED, TAG_SEMANTIC, text_fingerprint,
)
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import CourseQuestion, DupGroupDecision
from app.services.scope import resolve_read_scope, where_tenant

router = APIRouter(prefix="/quarantine", tags=["tcap4-quarantine"])

_ROLES = (Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)

_SUFFIX_QUARANTINE = f":{TAG_MANUAL}"      # 通用打标待人工（manual_review）
_SUFFIX_SEMANTIC = f":{TAG_SEMANTIC}"      # 语义近似重复，三链路一律打标待裁决
_SUFFIX_ARCHIVED = f":{TAG_ARCHIVED}"      # 已归档，移出待办


def _is_flagged_tag(tag: Optional[str]) -> bool:
    """两种「仅打标、仍待人工」后缀都算队列内（语义近似不受 tag_manual 口径限制）。"""
    value = str(tag or "")
    return value.endswith(_SUFFIX_QUARANTINE) or value.endswith(_SUFFIX_SEMANTIC)


def _flagged_like() -> Any:
    return or_(
        CourseQuestion.source_tag.like(f"%{_SUFFIX_QUARANTINE}"),
        CourseQuestion.source_tag.like(f"%{_SUFFIX_SEMANTIC}"),
    )

#: source_tag 前缀 → 链路名（异常队列按链路分组，便于定位是哪条入库口来的脏题）
_CHAIN_OF = {"auto_crawl": "crawl", "teacher": "teacher", "import": "import"}

#: gate → 中文名。与 ``app/core/intake_guard.py`` 各闸返回的 gate 值一一对应；
#: 未收录的新 gate 前端按原值兜底显示，不会丢信息。
GATE_LABEL = {
    "dedup_exact": "内容哈希重复",
    "dedup_text": "题干指纹重复",
    "dedup_semantic": "语义近似重复（待人工裁决）",
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
        "dup_of": dup_of if dup_of is not None else getattr(row, "guard_dup_of", None),
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
        select(CourseQuestion.id, CourseQuestion.content_hash,
               CourseQuestion.text_fp, CourseQuestion.stem)
    )).all()
    by_hash: Dict[str, List[int]] = {}
    by_fp: Dict[str, List[int]] = {}
    for qid, ch, fp, stem in rows:
        if ch:
            by_hash.setdefault(str(ch), []).append(qid)
        fp = fp or text_fingerprint(stem)  # 迁移 0027 前存量行兜底
        if fp:
            by_fp.setdefault(str(fp), []).append(qid)
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
        _flagged_like(),
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
        elif _is_flagged_tag(tag):
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
        stmt = stmt.where(_flagged_like())
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
            fp = row.text_fp or text_fingerprint(row.stem)
            others = [i for i in by_fp.get(fp or "", []) if i != row.id]
            dup_of = others[0] if others else None
        out.append(_item_out(row, dup_of))
    return out


class ResolveBody(BaseModel):
    action: str  # release / publish / archive


class BatchResolveBody(BaseModel):
    ids: List[int]
    action: str  # release / publish / archive


# ---------------------------------------------------------------- 真题重复组裁决
def _group_key_of(prefix: str, value: Any) -> Optional[str]:
    """组键：fp:<text_fp> / hash:<content_hash>；与页面 URL 直接互转。"""
    v = str(value or "").strip()
    return f"{prefix}:{v}" if v else None


def _match_group_key(row: CourseQuestion, group_key: str) -> bool:
    if group_key.startswith("fp:"):
        fp = row.text_fp or text_fingerprint(row.stem)
        return group_key == f"fp:{fp}"
    if group_key.startswith("hash:"):
        return group_key == f"hash:{row.content_hash}"
    return False


async def _group_members(
    db: AsyncSession, *, group_key: str, mode: str, tenant: Optional[str],
    tenants: Optional[List[str]], statuses: Sequence[str],
) -> List[CourseQuestion]:
    """按组键在指定数据范围内重新取成员——绝不信任前端传来的 id 列表。"""
    stmt = select(CourseQuestion).where(CourseQuestion.status.in_(list(statuses)))
    if mode == "tenant":
        stmt = stmt.where(CourseQuestion.tenant_id == tenant)
    elif mode == "tenants":
        stmt = stmt.where(CourseQuestion.tenant_id.in_(tenants or []))
    rows = (await db.execute(stmt.order_by(CourseQuestion.id))).scalars().all()
    return [r for r in rows if _match_group_key(r, group_key)]


@router.get("/duplicate-groups")
async def duplicate_groups(
    status: str = Query("published", description="默认只看真题重复组（published）"),
    q: Optional[str] = Query(None, description="题干关键词"),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """列出待人工裁决的重复组（按持久化 text_fp 分组，同 hash 也可识别）。

    **不自动改任何题目**：这里只把「哪几条像同一道题」摆出来，决定留在库里的是
    哪条由人在页面上逐组点选（``POST /duplicate-groups/{key}/resolve``）。已裁决
    的组不再返回，但题目本身一条都不自动删、不自动清组卷池。
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()] or ["published"]
    scope = await resolve_read_scope(user, db)
    stmt = select(CourseQuestion).where(
        CourseQuestion.status.in_(statuses),
        or_(CourseQuestion.source_tag.is_(None), ~CourseQuestion.source_tag.like(f"%{_SUFFIX_ARCHIVED}")),
    )
    stmt = where_tenant(stmt, CourseQuestion.tenant_id, scope)
    if q:
        stmt = stmt.where(CourseQuestion.stem.ilike(f"%{q.strip()}%"))
    rows = (await db.execute(stmt.order_by(CourseQuestion.id))).scalars().all()

    decided = set((await db.execute(select(DupGroupDecision.group_key))).scalars().all())
    buckets: Dict[str, List[CourseQuestion]] = {}
    for row in rows:
        key = (
            _group_key_of("fp", row.text_fp or text_fingerprint(row.stem))
            or _group_key_of("hash", row.content_hash)
        )
        if key and key not in decided:
            buckets.setdefault(key, []).append(row)

    out = []
    for key, members in buckets.items():
        if len(members) < 2:
            continue
        out.append({
            "group_key": key,
            "size": len(members),
            "subject": members[0].subject,
            "year": members[0].year,
            "stem": members[0].stem,
            "members": [_item_out(m) for m in members],
        })
        if len(out) >= limit:
            break
    return out


class DuplicateGroupResolveBody(BaseModel):
    action: str            # keep_one（保留一条、其余归档）| keep_all（确认全留，不改动题目）
    keep_id: Optional[int] = None


@router.post("/duplicate-groups/{group_key}/resolve")
async def resolve_duplicate_group(
    group_key: str,
    body: DuplicateGroupResolveBody,
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """人工逐组裁决：指定保留哪一条，其余**归档移出队列**（保留数据，不硬删）。

    ``keep_all`` 表示教研确认这组不是重复（例如同一道题不同小问），不碰任何题目，
    只写裁决留痕让该组从待办消失。两种动作都写 ``dup_group_decisions``，可追溯谁在
    什么时候判的。组内成员按当前用户数据范围重新计算，前端伪造 id 会被 422 拒。
    """
    action = (body.action or "").strip()
    if action not in ("keep_one", "keep_all"):
        raise HTTPException(status_code=422, detail="action 只支持 keep_one / keep_all")
    if not group_key.startswith(("fp:", "hash:")):
        raise HTTPException(status_code=422, detail="group_key 格式错误（应为 fp: 或 hash: 前缀）")

    exists = (await db.execute(
        select(DupGroupDecision).where(DupGroupDecision.group_key == group_key)
    )).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="该重复组已裁决")

    scope = await resolve_read_scope(user, db)
    mode = scope.get("mode")
    members = await _group_members(
        db, group_key=group_key, mode=mode,
        tenant=scope.get("tenant"), tenants=scope.get("tenants"),
        statuses=["published", "draft"],
    )
    if len(members) < 2:
        raise HTTPException(status_code=404, detail="重复组不存在或超出当前数据范围")
    ids = [m.id for m in members]
    if action == "keep_one" and body.keep_id not in ids:
        raise HTTPException(status_code=422, detail="keep_id 不在该重复组内")

    if action == "keep_one":
        now = datetime.now(timezone.utc)
        for row in members:
            if row.id == body.keep_id:
                continue
            base = _base_of(row.source_tag)
            row.status = "quarantine"
            row.source_tag = f"{base}:{TAG_ARCHIVED}"
            row.guard_gate = "dup_group_archived"
            row.guard_reason = f"人工裁决重复组：保留 #{body.keep_id}，本条归档（不删数据）"
            row.guard_dup_of = body.keep_id
            row.reviewed_by = user.get("user_id")
            row.reviewed_at = now

    db.add(DupGroupDecision(
        group_key=group_key, decision=action,
        keep_id=body.keep_id if action == "keep_one" else None,
        member_ids=ids, reviewed_by=user.get("user_id"),
        reviewed_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return {
        "group_key": group_key, "action": action,
        "keep_id": body.keep_id if action == "keep_one" else None,
        "archived_ids": [m.id for m in members if action == "keep_one" and m.id != body.keep_id],
        "member_ids": ids, "reviewed_by": user.get("user_id"),
    }


@router.post("/batch-resolve")
async def batch_resolve(
    body: BatchResolveBody,
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """逐条处置一批异常题：**部分成功**也要如实回报，不因一条失败回滚整批。

    ``resolve`` 的单条校验（范围/状态/答案合规）逐条复用；返回 ``ok`` 与
    ``skipped`` 两个列表，每条 skipped 带 ``id``/``status``/``detail``，前端可
    先批量操作再对失败项单独处理。一次最多 100 条，避免误操作放大。
    """
    ids = list(dict.fromkeys(body.ids or []))  # 去重且保序
    if not ids or len(ids) > 100:
        raise HTTPException(status_code=422, detail="ids 需为 1~100 条")
    ok: List[dict] = []
    skipped: List[dict] = []
    scope = await resolve_read_scope(user, db)
    for qid in ids:
        stmt = where_tenant(
            select(CourseQuestion).where(CourseQuestion.id == qid),
            CourseQuestion.tenant_id, scope,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            skipped.append({"id": qid, "status": 404, "detail": "question not found"})
            continue
        try:
            out = _resolve_one(row, body.action, user)
            ok.append(out)
        except HTTPException as e:
            skipped.append({"id": qid, "status": e.status_code, "detail": str(e.detail)})
    await db.commit()
    return {"action": body.action, "ok": ok, "skipped": skipped,
            "total": len(ids), "succeeded": len(ok)}


def _resolve_one(row: CourseQuestion, action: str, user: dict) -> dict:
    """单条处置的**纯改写**部分（不提交），单条与批量共用同一口径。"""
    action = (action or "").strip()
    if action not in ("release", "publish", "archive"):
        raise HTTPException(status_code=422, detail="action 只支持 release / publish / archive")
    if not _is_pending_quarantine(row) and not _is_flagged_tag(row.source_tag):
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
    row.guard_dup_of = None
    row.reviewed_by = user.get("user_id")
    row.reviewed_at = datetime.now(timezone.utc)
    return {
        "id": row.id, "action": action, "status": row.status,
        "source_tag": row.source_tag,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
    }


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
    scope = await resolve_read_scope(user, db)
    stmt = where_tenant(
        select(CourseQuestion).where(CourseQuestion.id == question_id),
        CourseQuestion.tenant_id, scope,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="question not found")
    out = _resolve_one(row, body.action, user)
    await db.commit()
    return out
