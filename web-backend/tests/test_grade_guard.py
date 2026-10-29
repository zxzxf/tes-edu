"""判分护栏（M2-10 / G3）口径回归：同一标答累计 K 次改判 → 冻结该题。

两层验证：

1. **纯规则**（不碰 DB）：指纹、有效改判判定、阈值；
2. **真库**（sqlite 内存库，与 ``test_rbac_integration`` 同一套基建）：
   计数 → 冻结 → 判分侧生效 → 解冻 → 复发，以及两个最容易写错的地方：
   - 重复提交同一判定**不计**（否则三次双击就误伤）；
   - 冻结按 ``(content_hash, 标答指纹)`` 跨卷生效，答案一改自动失效。
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.grade_guard import (
    ALERT_FREEZE,
    ALERT_REFREEZE,
    ALERT_RELEASE,
    STATUS_FROZEN,
    STATUS_RELEASED,
    STATUS_WATCHING,
    answer_fingerprint,
    freeze_message,
    is_effective_flip,
    student_frozen_reason,
    threshold,
    verdict_of,
)
from app.models import ops as _ops  # noqa: F401  注册 exam 域表
from app.models.course_kb import Base
from app.models.ops import (
    ExamGradingAlert,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmission,
    ExamSubmissionItem,
)
from app.services import grading_guard as guard

PILOT = "school_pilot_01"


# ------------------------------------------------------------------ 纯规则
def test_fingerprint_ignores_writing_style():
    """同一份答案在库里会出现的书写差异（空白/大小写/全角）必须同指纹。"""
    base = answer_fingerprint("sqrt(66)/6")
    assert answer_fingerprint("  SQRT(66)/6 ") == base
    assert answer_fingerprint("sqrt（66）／6") == base      # 全角括号与斜杠
    assert answer_fingerprint("sqrt(66)/3") != base


def test_fingerprint_is_textual_not_semantic():
    """指纹只做书写层归一，**不做语义等价展开**（这是刻意设计）。

    题库里同一道题的答案文本是同一行数据，跨卷快照逐字一致，指纹天然相同。
    若把 ``\\dfrac32`` 与 ``3/2`` 强行并成一个键，反而会把「内容相同、答案写法
    不同」的两道变式题当成同一份答案一起冻结。语义等价交给 ``equiv`` /
    ``answer_values`` 负责，指纹只回答「标答文本有没有被改过」。
    """
    assert answer_fingerprint(r"\dfrac32") != answer_fingerprint("3/2")


def test_verdict_mapping_and_effective_flip():
    assert verdict_of(True, False) == "correct"
    assert verdict_of(False, True) == "partial"
    assert verdict_of(False, False) == "wrong"
    assert verdict_of(None, None) == "wrong"
    # 判定没变 = 不是改判（重复保存 / 补评语）
    assert is_effective_flip((True, False), (True, False)) is False
    assert is_effective_flip((False, False), (False, True)) is True
    assert is_effective_flip((False, True), (False, False)) is True
    assert is_effective_flip((True, False), (False, False)) is True


def test_threshold_default_and_override(monkeypatch):
    assert threshold() == 3
    monkeypatch.setattr(settings, "grading_freeze_threshold", 5)
    assert threshold() == 5
    monkeypatch.setattr(settings, "grading_freeze_threshold", 0)  # 非法值退回默认
    assert threshold() == 3


def test_student_reason_does_not_blame_student():
    msg = student_frozen_reason()
    assert "复核" in msg
    assert "判错" not in msg          # 别让全班看到「你被判错了」


def test_freeze_message_mentions_counts():
    assert "3 次改判" in freeze_message(3, 3)
    assert "解冻后再次" in freeze_message(3, 3, refreeze=True)


def test_grade_one_shortcircuits_on_frozen_question():
    """冻结必须**先于判分**生效：学生答对了也不给分、也不判错，直接转人工。"""
    from types import SimpleNamespace

    from app.api.exam import _grade_one

    q = SimpleNamespace(
        id=1, seq=3, qtype="choice", multi=False, answer="A",
        options={"A": "1", "B": "2"}, content_hash="h1",
    )
    it = _grade_one(q, "A")
    assert it["status"] == "auto" and it["correct"] is True   # 未冻结：正常机判

    frozen = _grade_one(q, "A", frozen_reason=student_frozen_reason())
    assert frozen["status"] == "manual_pending"
    assert frozen["correct"] is None                          # 不判错
    assert frozen["reason"] == student_frozen_reason()


# ------------------------------------------------------------------ 真库
async def _bootstrap():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_question(
    db: AsyncSession,
    *,
    answer: str = "sqrt(66)/6",
    content_hash: str = "h-1",
    seq: int = 1,
    qtype: str = "blank",
    tenant: str = PILOT,
):
    p = ExamPaper(tenant_id=tenant, title="护栏测试卷", paper_type="pure_blank", published=True)
    db.add(p)
    await db.flush()
    q = ExamPaperQuestion(
        paper_id=p.id, seq=seq, content_hash=content_hash, qtype=qtype,
        stem="题干", answer=answer,
    )
    db.add(q)
    await db.flush()
    return p, q


async def _flip(
    db, q, paper, *, before=(False, False), after=(True, False), actor="t1",
    was_reviewed=True,
):
    return await guard.register_flip(
        db, tenant_id=PILOT, question=q, before=before, after=after,
        was_reviewed=was_reviewed, actor_id=actor, paper_id=paper.id,
    )


def _run(fn):
    """项目未装 pytest-asyncio，统一用 asyncio.run 跑（与既有测试一致）。"""

    def wrapper():
        asyncio.run(fn())

    return wrapper


@_run
async def test_flips_freeze_at_threshold_and_alert_once():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        f1 = await _flip(db, q, p)
        assert (f1.status, f1.flips) == (STATUS_WATCHING, 1)
        f2 = await _flip(db, q, p, actor="t2")
        assert (f2.status, f2.flips) == (STATUS_WATCHING, 2)
        assert f2.actors == ["t1", "t2"]
        assert (await guard.frozen_reasons(db, PILOT, [q])) == {}   # 未到阈值不冻结

        f3 = await _flip(db, q, p, actor="t3")
        assert (f3.status, f3.flips) == (STATUS_FROZEN, 3)
        alerts = (await db.execute(select(ExamGradingAlert))).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].kind == ALERT_FREEZE and alerts[0].flips == 3
        assert alerts[0].affected_count == 0            # 还没有人按它被判错

        # 冻结后判分侧立刻生效
        assert guard.reason_for(await guard.frozen_reasons(db, PILOT, [q]), q) == (
            student_frozen_reason()
        )

        # 继续改判只涨计数，不重复告警（否则老师一天收十条同样的通知）
        await _flip(db, q, p, before=(True, False), after=(False, False), actor="t4")
        assert (await db.execute(select(ExamGradingAlert))).scalars().all().__len__() == 1
    await engine.dispose()


@_run
async def test_first_judgment_never_counts():
    """**首次判定不算改判**。

    转人工的题本来就得老师从无到有判一次。若把「第一次判」算成改判，任何一道
    填空题上来三个学生就会冻结 —— 护栏会立刻变成全站停摆（这是本模块最危险的
    一个写法错误，2026-10-30 设计复核时改掉）。
    """
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        for i in range(6):
            assert await _flip(db, q, p, actor=f"t{i}", was_reviewed=False) is None
        assert (await db.execute(select(ExamGradingAlert))).scalars().all() == []
        assert (await guard.frozen_reasons(db, PILOT, [q])) == {}
    await engine.dispose()


@_run
async def test_duplicate_verdict_does_not_consume_budget():
    """重复提交同一判定不算改判 —— 否则三次双击就触发冻结，护栏天天误伤。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        for _ in range(5):
            assert await _flip(db, q, p, before=(True, False), after=(True, False)) is None
        rows = (await db.execute(select(ExamGradingAlert))).scalars().all()
        assert rows == []
        assert (await guard.frozen_reasons(db, PILOT, [q])) == {}
    await engine.dispose()


@_run
async def test_freeze_applies_across_papers_same_content_hash():
    """同一道题在另一张卷里是**另一行** paper_question，冻结必须同样生效。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p1, q1 = await _seed_question(db)
        p2, q2 = await _seed_question(db, content_hash="h-1", seq=2)   # 同题另卷
        for i in range(3):
            await _flip(db, q1, p1, actor=f"t{i}")
        reasons = await guard.frozen_reasons(db, PILOT, [q1, q2])
        assert guard.reason_for(reasons, q1) == student_frozen_reason()
        assert guard.reason_for(reasons, q2) == student_frozen_reason()
        # 别的租户不受影响
        assert (await guard.frozen_reasons(db, "other_school", [q1])) == {}
    await engine.dispose()


@_run
async def test_answer_change_moves_to_new_key_and_unfreezes_automatically():
    """标答改了就不再是「同一答案」：新指纹从 0 观察，旧冻结记录不再拦住它。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        for i in range(3):
            await _flip(db, q, p, actor=f"t{i}")
        assert guard.reason_for(await guard.frozen_reasons(db, PILOT, [q]), q)

        q.answer = "sqrt(6)/3"          # 题库把标答改对了
        await db.flush()
        assert (await guard.frozen_reasons(db, PILOT, [q])) == {}

        # 新指纹重新计数，不继承旧的 3 次
        f = await _flip(db, q, p, actor="t9")
        assert (f.status, f.flips) == (STATUS_WATCHING, 1)
        assert f.answer_fingerprint == answer_fingerprint("sqrt(6)/3")
    await engine.dispose()


@_run
async def test_affected_count_only_counts_auto_wrong():
    """影响面只数「被机器自动判为错」的提交：人工判的、判对的不算。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        sub = ExamSubmission(
            assignment_id=1, paper_id=p.id, student_id="s1", tenant_id=PILOT,
            status="graded", score=0, correct_count=0, total_count=3,
        )
        db.add(sub)
        await db.flush()
        db.add_all([
            ExamSubmissionItem(   # 被机器判错 —— 该算
                submission_id=sub.id, paper_question_id=q.id, seq=1,
                qtype="blank", student_answer="2sqrt(6)/3", correct=False,
                partial=False, status="auto",
            ),
            ExamSubmissionItem(   # 机器判对 —— 不算
                submission_id=sub.id, paper_question_id=q.id, seq=1,
                qtype="blank", student_answer="sqrt(66)/6", correct=True,
                partial=False, status="auto",
            ),
            ExamSubmissionItem(   # 人工判的 —— 不算
                submission_id=sub.id, paper_question_id=q.id, seq=1,
                qtype="blank", student_answer="x", correct=False,
                partial=False, status="manual_reviewed",
            ),
        ])
        await db.flush()

        assert await guard.affected_count(
            db, PILOT, q.content_hash, answer_fingerprint(q.answer)
        ) == 1
        for i in range(3):
            row = await _flip(db, q, p, actor=f"t{i}")
        assert row.affected_count == 1
        alert = (await db.execute(select(ExamGradingAlert))).scalars().all()[-1]
        assert alert.affected_count == 1
    await engine.dispose()


@_run
async def test_release_then_refreeze_is_marked_refreeze():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db)
        for i in range(3):
            row = await _flip(db, q, p, actor=f"t{i}")
        assert row.status == STATUS_FROZEN

        await guard.release_freeze(
            db, row, {"user_id": "head_teacher", "role": "teacher"}, "标答无误，恢复机判"
        )
        assert row.status == STATUS_RELEASED and row.flips == 0
        assert row.release_count == 1
        assert (await guard.frozen_reasons(db, PILOT, [q])) == {}   # 已恢复自动判分

        # 复发：重新从 1 计，攒够再冻，且标 refreeze（说明这题反复出问题）
        for i in range(3):
            row = await _flip(db, q, p, actor=f"t{i}")
        assert row.status == STATUS_FROZEN and row.release_count == 1
        kinds = [
            a.kind for a in (await db.execute(select(ExamGradingAlert))).scalars().all()
        ]
        assert kinds == [ALERT_FREEZE, ALERT_RELEASE, ALERT_REFREEZE]
    await engine.dispose()


@_run
async def test_question_without_content_hash_never_freezes():
    """老数据缺 content_hash 时按行冻结只会造成「假冻结」，宁可不冻（照旧人工兜底）。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed_question(db, content_hash=None)
        for i in range(5):
            assert await _flip(db, q, p, actor=f"t{i}") is None
        assert (await db.execute(select(ExamGradingAlert))).scalars().all() == []
    await engine.dispose()


@_run
async def test_list_freezes_defaults_to_frozen_and_counts():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q1 = await _seed_question(db, content_hash="h-a")
        p2, q2 = await _seed_question(db, content_hash="h-b")
        for i in range(3):
            await _flip(db, q1, p, actor=f"t{i}")
        await _flip(db, q2, p2, actor="t0")        # 只 1 次，仍在观察

        rows, counts, total = await guard.list_freezes(db, PILOT, STATUS_FROZEN)
        assert total == 1 and len(rows) == 1 and rows[0].content_hash == "h-a"
        assert counts[STATUS_FROZEN] == 1 and counts[STATUS_WATCHING] == 1

        rows_all, _, total_all = await guard.list_freezes(db, PILOT, "all")
        assert total_all == 2
        assert {r.status for r in rows_all} == {STATUS_FROZEN, STATUS_WATCHING}
    await engine.dispose()
