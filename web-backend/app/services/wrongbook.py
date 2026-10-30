"""学生端错题本：云同步（增量拉取 / 幂等 upsert / 软删 tombstone）+ 统计。

同步协议要点（与 App 端 `WrongSyncMerger` 配套）：
- **client_id 是幂等键**：离线新建的题重复上传不得变成两条；
- **updated_at 大者胜**：客户端与服务端各自都可能改过，按时间戳判胜负；
- **删除走软删**：物理删除会让客户端"不知道服务端删过"，同步时又把旧数据推回来，
  所以删除置 deleted_at 并在拉取时下发 tombstone。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import ExamPaperQuestion, ExamSubmissionItem, StudentMastery, WrongQuestion

SYNC_LIMIT = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ms(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp() * 1000) if dt else None


def _from_ms(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def wrong_out(w: WrongQuestion) -> dict:
    """错题 → 客户端契约（时间统一 epoch 毫秒）。"""
    return {
        "id": w.id,
        "client_id": w.client_id,
        "question": w.question,
        "my_answer": w.wrong_answer,
        "correct_answer": w.correct_answer,
        "knowledge_point": w.knowledge_point,
        "error_type": w.error_type,
        "comment": w.comment,
        "correction": w.correction,
        "first_wrong_step": w.first_wrong_step,
        "created_at": _ms(w.created_at) or 0,
        "updated_at": _ms(w.updated_at) or _ms(w.created_at) or 0,
        "deleted_at": _ms(w.deleted_at),
        "source": w.source,
    }


async def _apply_exam_wrong(
    db: AsyncSession,
    student_id: str,
    item: ExamSubmissionItem,
    question: ExamPaperQuestion,
    row: Optional[WrongQuestion],
    now: datetime,
) -> None:
    """Apply one exam submission item to its wrongbook row (in caller's transaction).

    与单题版 `sync_exam_wrong_question` 完全同义，只是 `row` 由调用方预先查出、
    `now` 由调用方统一传入，从而支持批量场景下一次取所有行、内存里逐个应用。
    """
    if item.correct is None:
        return
    client_id = f"exam:{item.id}"
    if row is None and item.correct:
        return
    if row is None:
        row = WrongQuestion(student_id=student_id, client_id=client_id, source="exam")
        db.add(row)
    if item.correct:
        row.deleted_at = now
        row.updated_at = now
        return
    row.question = question.stem
    row.wrong_answer = item.student_answer or ""
    row.correct_answer = question.answer or ""
    row.content_hash = question.content_hash
    kps = question.knowledge_points or []
    row.knowledge_point = kps[0][:128] if kps and isinstance(kps[0], str) else ""
    row.deleted_at = None
    row.updated_at = now
    row.source = "exam"


async def sync_exam_wrong_question(
    db: AsyncSession,
    student_id: str,
    item: ExamSubmissionItem,
    question: ExamPaperQuestion,
) -> None:
    """Keep one server-owned wrongbook row per exam item in the caller's transaction."""
    if item.correct is None:
        return
    client_id = f"exam:{item.id}"
    row = (
        await db.execute(select(WrongQuestion).where(
            WrongQuestion.student_id == student_id,
            WrongQuestion.client_id == client_id,
        ))
    ).scalars().first()
    await _apply_exam_wrong(db, student_id, item, question, row, _utcnow())


async def sync_exam_wrong_questions(
    db: AsyncSession,
    student_id: str,
    pairs: Sequence[Tuple[ExamSubmissionItem, ExamPaperQuestion]],
) -> None:
    """批量版：一次性 `IN` 查询取出所有既有行，内存里逐个应用。

    用于考试提交接口——一份答卷可能一次带很多题，原逐题 `select` 会变成 N 次
    往返；这里合并成 1 次查询 + 内存循环，语义与单题版逐题调用完全一致。
    `pairs` 为 (item, question) 列表，通常只传判错的题（语义见单题版）。
    """
    if not pairs:
        return
    client_ids = [f"exam:{it.id}" for it, _ in pairs]
    existing = (
        await db.execute(
            select(WrongQuestion).where(
                WrongQuestion.student_id == student_id,
                WrongQuestion.client_id.in_(client_ids),
            )
        )
    ).scalars().all()
    by_cid = {w.client_id: w for w in existing}
    now = _utcnow()
    for item, question in pairs:
        await _apply_exam_wrong(
            db, student_id, item, question, by_cid.get(f"exam:{item.id}"), now
        )


async def list_sync(
    db: AsyncSession,
    student_id: str,
    since_ms: int = 0,
    kp: Optional[str] = None,
    error_type: Optional[str] = None,
    limit: int = SYNC_LIMIT,
) -> List[dict]:
    """增量拉取：未删记录 + since 之后的 tombstone（让客户端能跟着删）。"""
    since = _from_ms(since_ms)
    limit = max(1, min(int(limit or SYNC_LIMIT), 500))

    stmt = select(WrongQuestion).where(WrongQuestion.student_id == student_id)
    if kp:
        stmt = stmt.where(WrongQuestion.knowledge_point == kp)
    if error_type:
        stmt = stmt.where(WrongQuestion.error_type == error_type)

    live = stmt.where(WrongQuestion.deleted_at.is_(None))
    if since:
        live = live.where(
            (WrongQuestion.updated_at.is_(None)) | (WrongQuestion.updated_at >= since)
            | (WrongQuestion.created_at >= since)
        )
    rows = (await db.execute(live.order_by(WrongQuestion.id.asc()).limit(limit))).scalars().all()

    tombstones: List[WrongQuestion] = []
    if since:
        tstmt = select(WrongQuestion).where(
            WrongQuestion.student_id == student_id,
            WrongQuestion.deleted_at.is_not(None),
            WrongQuestion.deleted_at >= since,
        )
        tombstones = (await db.execute(tstmt.limit(limit))).scalars().all()

    return [wrong_out(w) for w in list(rows) + list(tombstones)]


async def upsert_sync(
    db: AsyncSession,
    student_id: str,
    items: Sequence[dict],
    tenant_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[dict]:
    """批量 upsert（含软删）。

    返回该学生**当前全量**未删记录 + 本次涉及的 tombstone——客户端拿到后
    直接整体合并，不必自己推断"哪些成功了"。
    """
    now = now or _utcnow()
    for it in items or []:
        cid = (it.get("client_id") or "").strip()
        if cid.startswith("exam:"):
            continue
        question = (it.get("question") or "").strip()
        if not question:
            continue

        row = None
        if cid:
            row = (
                await db.execute(
                    select(WrongQuestion).where(
                        WrongQuestion.student_id == student_id,
                        WrongQuestion.client_id == cid,
                    )
                )
            ).scalars().first()
        elif it.get("id"):
            row = (
                await db.execute(
                    select(WrongQuestion).where(
                        WrongQuestion.id == int(it["id"]),
                        WrongQuestion.student_id == student_id,
                    )
                )
            ).scalars().first()

        if row is not None and row.source == "exam":
            continue

        incoming_ms = int(it.get("updated_at") or 0)

        if row is None:
            if it.get("deleted"):
                continue        # 本地新建后又删掉：服务端从来没有，不必留 tombstone
            row = WrongQuestion(
                student_id=student_id,
                client_id=cid or None,
                question=question,
                wrong_answer=it.get("my_answer"),
                correct_answer=it.get("correct_answer"),
                knowledge_point=it.get("knowledge_point"),
                error_type=it.get("error_type"),
                comment=it.get("comment"),
                correction=it.get("correction"),
                first_wrong_step=it.get("first_wrong_step"),
                source=(it.get("source") or "app"),
                created_at=_from_ms(incoming_ms) or now,
                updated_at=now,
            )
            db.add(row)
            continue

        # 已存在：先看删除，再看时间戳（客户端更旧就不覆盖服务端的改动）
        if it.get("deleted"):
            if row.deleted_at is None:
                row.deleted_at = now
                row.updated_at = now
            continue

        existing_ms = _ms(row.updated_at) or _ms(row.created_at) or 0
        if incoming_ms and existing_ms and incoming_ms < existing_ms:
            continue

        row.question = question
        row.wrong_answer = it.get("my_answer") or row.wrong_answer
        row.correct_answer = it.get("correct_answer") or row.correct_answer
        row.knowledge_point = it.get("knowledge_point") or row.knowledge_point
        row.error_type = it.get("error_type") or row.error_type
        row.comment = it.get("comment") or row.comment
        row.correction = it.get("correction") or row.correction
        if it.get("first_wrong_step") is not None:
            row.first_wrong_step = it["first_wrong_step"]
        row.deleted_at = None
        row.updated_at = now

    await db.commit()
    return await list_sync(db, student_id, since_ms=0)


async def soft_delete(db: AsyncSession, student_id: str, wrong_id: int) -> bool:
    """软删一条（物理删除会破坏同步的 tombstone 语义）。"""
    row = (
        await db.execute(
            select(WrongQuestion).where(
                WrongQuestion.id == wrong_id, WrongQuestion.student_id == student_id
            )
        )
    ).scalars().first()
    if row is None:
        return False
    row.deleted_at = _utcnow()
    row.updated_at = row.deleted_at
    await db.commit()
    return True


async def stats(db: AsyncSession, student_id: str) -> dict:
    """错题概览：总数 / 错因分布 / 知识点分布 / 掌握度。

    mastery 取 student_mastery 的 correct/(correct+wrong)；
    delta 需要掌握度快照表才能算真实的"较上周变化"，当前无该表故恒为 0.0
    （字段先留着，客户端已按"为 0 就不展示"处理）。
    """
    base = select(WrongQuestion).where(
        WrongQuestion.student_id == student_id, WrongQuestion.deleted_at.is_(None)
    )
    total = int((await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one())

    rows = (await db.execute(base)).scalars().all()
    by_error: dict = {}
    by_kp: dict = {}
    for w in rows:
        if w.error_type:
            by_error[w.error_type] = by_error.get(w.error_type, 0) + 1
        if w.knowledge_point:
            by_kp[w.knowledge_point] = by_kp.get(w.knowledge_point, 0) + 1

    mastery = []
    mrows = (
        await db.execute(
            select(StudentMastery).where(StudentMastery.student_id == student_id)
        )
    ).scalars().all()
    for m in mrows:
        done = int(m.correct_count or 0) + int(m.wrong_count or 0)
        if done <= 0:
            continue        # 未判分的知识点不显示掌握度（避免假 0%）
        mastery.append(
            {
                "kp": m.knowledge_point,
                "mastery": round(int(m.correct_count or 0) / done, 4),
                "delta": 0.0,
            }
        )
    mastery.sort(key=lambda x: x["mastery"])

    return {
        "total": total,
        "by_error_type": by_error,
        "by_knowledge_point": by_kp,
        "mastery": mastery,
    }
