"""判分指标看板（M2-7）口径回归。

两层：
1. **纯函数**（``app/core/grading_metrics.py``）：比率、达标判定（注意「越低越好」
   与「越高越好」是两套函数）、分位数、来源标签；
2. **真库**（sqlite 内存库）：造 4 道题（选择/填空/转人工填空/已复核解答）跑一遍
   聚合，验证面板上的每个数字都对得上，且**窗口过滤真的生效**。

这里最值得防的回归是方向写反：把「人工复核率越低越好」套进「越高越好」的阈值
函数，会把 3% 判成不达标、把 90% 判成达标——面板全绿而实际已经烂了。
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.grading_metrics import (
    SOURCE_BLANK_EQUIV,
    SOURCE_CHOICE,
    SOURCE_LABELS,
    SOURCE_MANUAL_FROZEN,
    SOURCE_MANUAL_SUBJECTIVE,
    SOURCE_MANUAL_SYMBOL,
    SOURCE_UNKNOWN,
    accuracy_metric,
    coverage_metric,
    grade_status,
    grade_status_lower,
    is_manual_source,
    manual_rate_metric,
    percentile,
    ratio,
    source_label,
    source_rows,
)
from app.models import ops as _ops  # noqa: F401  注册 exam 域表
from app.models.course_kb import Base
from app.models.ops import (
    ExamAnswerFreeze,
    ExamGradingRevision,
    ExamGradingTicket,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmission,
    ExamSubmissionItem,
)
from app.services import grading_metrics

PILOT = "school_pilot_01"


def _run(fn):
    def wrapper():
        asyncio.run(fn())

    return wrapper


# ------------------------------------------------------------------ 纯函数
def test_ratio_does_not_confuse_zero_with_zero_percent():
    """0/0 不是 0%。无数据渲染成 0% 会让老师以为判据全挂了。"""
    assert ratio(0, 0) is None
    assert ratio(5, 0) is None
    assert ratio(1, 3) == 0.333333
    assert ratio(0, 10) == 0.0


def test_coverage_metric_thresholds():
    assert coverage_metric(100, 100)["status"] == "ok"
    assert coverage_metric(99, 100)["status"] == "ok"        # 恰好 99% 算达标
    assert coverage_metric(95, 100)["status"] == "watch"     # 首月过渡可接受
    assert coverage_metric(80, 100)["status"] == "below"
    empty = coverage_metric(0, 0)
    assert empty["value"] is None and empty["status"] == "unknown"


def test_manual_rate_direction_is_reversed():
    """人工复核率**越低越好**。方向写反会让面板「全绿而实际烂透」。"""
    assert manual_rate_metric(3, 100)["status"] == "ok"       # ≤3% 达标
    assert manual_rate_metric(10, 100)["status"] == "watch"   # ≤10% 首月可接受
    assert manual_rate_metric(20, 100)["status"] == "below"
    # 对照：同一组数值用「越高越好」的函数会得到完全相反的结论
    assert grade_status(0.03, 0.03, 0.10) == "ok"
    assert grade_status(0.20, 0.03, 0.10) == "ok"             # 20% 被判达标（错）
    assert grade_status_lower(0.20, 0.03, 0.10) == "below"


def test_accuracy_is_not_fabricated_without_samples():
    """没有复核样本时不给百分比——伪造 100% 等于宣称「机器从没错过」。"""
    a = accuracy_metric(auto_count=120, reviewed_auto_count=0, overturned_count=0)
    assert a["measurable"] is False
    assert a["value"] is None
    assert a["status"] == "unknown"
    assert "复核" in a["note"]

    b = accuracy_metric(auto_count=120, reviewed_auto_count=100, overturned_count=2)
    assert b["measurable"] is True and b["value"] == 0.98
    assert b["status"] == "ok"


def test_percentile_linear_interpolation():
    assert percentile([], 50) is None
    assert percentile([12.0], 90) == 12.0
    # 线性插值：样本少时 P90 不是「最大值」，否则看着像在报最慢那一单
    assert percentile([10, 20], 50) == 15.0
    assert percentile([0, 10, 20, 30, 40], 90) == 36.0


def test_source_labels_cover_every_code():
    """新增判据编码却忘了加中文标签，面板会出现一串英文代号。"""
    for code in (
        SOURCE_CHOICE,
        SOURCE_BLANK_EQUIV,
        SOURCE_MANUAL_SYMBOL,
        SOURCE_MANUAL_FROZEN,
        SOURCE_MANUAL_SUBJECTIVE,
        SOURCE_UNKNOWN,
    ):
        assert code in SOURCE_LABELS
        assert source_label(code) != code
    assert source_label("not_a_code") == "not_a_code"     # 未知码原样返回，不吞


def test_manual_source_detection():
    assert is_manual_source(SOURCE_MANUAL_SYMBOL) is True
    assert is_manual_source(SOURCE_CHOICE) is False
    # 未知来源不算人工：历史数据不猜，否则会把 unknown 混进分子
    assert is_manual_source(SOURCE_UNKNOWN) is False
    assert is_manual_source(None) is False


def test_source_rows_sorts_unknown_last():
    rows = source_rows({SOURCE_UNKNOWN: 5, SOURCE_CHOICE: 9, SOURCE_MANUAL_SYMBOL: 2})
    assert [r["source"] for r in rows] == [
        SOURCE_CHOICE, SOURCE_MANUAL_SYMBOL, SOURCE_UNKNOWN
    ]
    assert rows[0]["ratio"] == 0.5625
    assert rows[1]["manual"] is True and rows[0]["manual"] is False


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


async def _seed(db: AsyncSession, *, tenant: str = PILOT, submitted_at=None):
    """造一张混合卷 + 一份提交（4 题：选择 / 填空 / 转人工填空 / 已复核解答）。"""
    p = ExamPaper(tenant_id=tenant, title="指标测试卷", paper_type="mixed", published=True)
    db.add(p)
    await db.flush()
    qs = []
    for seq, qtype in enumerate(("choice", "blank", "blank", "subjective"), start=1):
        q = ExamPaperQuestion(
            paper_id=p.id, seq=seq, content_hash=f"h{seq}", qtype=qtype,
            stem=f"第{seq}题", answer="2",
        )
        db.add(q)
        qs.append(q)
    await db.flush()

    sub = ExamSubmission(
        assignment_id=1, paper_id=p.id, student_id="stu-1", tenant_id=tenant,
        status="graded", score=2, correct_count=2, total_count=4,
        submitted_at=submitted_at or datetime.now(timezone.utc),
    )
    db.add(sub)
    await db.flush()

    plan = [
        ("choice", "auto", SOURCE_CHOICE, True),
        ("blank", "auto", SOURCE_BLANK_EQUIV, True),
        ("blank", "manual_pending", SOURCE_MANUAL_SYMBOL, None),
        ("subjective", "manual_reviewed", SOURCE_MANUAL_SUBJECTIVE, False),
    ]
    items = []
    for q, (qtype, status, source, correct) in zip(qs, plan):
        it = ExamSubmissionItem(
            submission_id=sub.id, paper_question_id=q.id, seq=q.seq, qtype=qtype,
            student_answer="2", correct=correct, partial=False, status=status,
            grading_source=source,
        )
        db.add(it)
        items.append(it)
    await db.flush()

    # 转人工那道题：开单（pending）；已复核那道题：开单 + 判定留痕
    t1 = ExamGradingTicket(
        tenant_id=tenant, submission_id=sub.id, submission_item_id=items[2].id,
        assignment_id=1, paper_id=p.id, paper_question_id=qs[2].id,
        student_id="stu-1", seq=3, qtype="blank", student_answer="x+1",
        reason="含字母", status="pending",
    )
    t2 = ExamGradingTicket(
        tenant_id=tenant, submission_id=sub.id, submission_item_id=items[3].id,
        assignment_id=1, paper_id=p.id, paper_question_id=qs[3].id,
        student_id="stu-1", seq=4, qtype="subjective", student_answer="略",
        reason="解答题", status="reviewed", verdict="wrong",
        reviewed_by="t1", reviewed_at=datetime.now(timezone.utc),
    )
    db.add_all([t1, t2])
    await db.flush()
    db.add(
        ExamGradingRevision(
            ticket_id=t2.id, submission_item_id=items[3].id, action="judge",
            actor_id="t1", before_correct=None, after_correct=False,
        )
    )
    await db.commit()
    return p, sub, items, t1, t2


@_run
async def test_collect_numbers_add_up():
    engine, sf = await _bootstrap()
    async with sf() as db:
        await _seed(db)
        m = await grading_metrics.collect(db, PILOT, days=30)

        assert m["items"]["total"] == 4
        assert m["items"]["by_qtype"]["subjective"] == 1
        assert m["items"]["gradable_total"] == 3          # 选择 + 2 填空
        assert m["items"]["auto_gradable"] == 2

        cov = m["metrics"]["coverage"]
        assert cov["numerator"] == 2 and cov["denominator"] == 3
        assert cov["value"] == 0.666667 and cov["status"] == "below"

        # 人工复核率 = 曾经需要人判（1 待 + 1 已复核）/ 4
        mr = m["metrics"]["manual_rate"]
        assert mr["numerator"] == 2 and mr["denominator"] == 4
        assert mr["value"] == 0.5

        # 机器判分准确率：没有复核样本 → 不可测，而不是 100%
        assert m["metrics"]["accuracy"]["measurable"] is False
        assert m["metrics"]["accuracy"]["value"] is None

        src = {s["source"]: s["count"] for s in m["sources"]}
        assert src == {
            SOURCE_CHOICE: 1, SOURCE_BLANK_EQUIV: 1,
            SOURCE_MANUAL_SYMBOL: 1, SOURCE_MANUAL_SUBJECTIVE: 1,
        }
        assert sum(s["count"] for s in m["sources"]) == m["items"]["total"]

        assert m["tickets"]["counts"] == {"pending": 1, "claimed": 0, "reviewed": 1}
        assert m["tickets"]["turnaround_minutes"]["n"] == 1
        assert m["revisions"]["first_judged"] == 1     # 首次判定不算改判
        assert m["revisions"]["effective_flips"] == 0

        assert len(m["by_paper"]) == 1
        row = m["by_paper"][0]
        assert row["gradable"] == 3 and row["auto"] == 2 and row["manual"] == 2
        assert row["coverage"] == 0.666667 and row["status"] == "below"
    await engine.dispose()


@_run
async def test_window_filter_and_all_time_mode():
    """``days=30`` 之外的老提交要排除；``days=0`` 是「全量口径」。"""
    engine, sf = await _bootstrap()
    old = datetime.now(timezone.utc) - timedelta(days=60)
    async with sf() as db:
        await _seed(db, submitted_at=old)
        win = await grading_metrics.collect(db, PILOT, days=30)
        assert win["items"]["total"] == 0
        assert win["metrics"]["coverage"]["value"] is None   # 无数据 → 不是 0%
        all_time = await grading_metrics.collect(db, PILOT, days=0)
        assert all_time["items"]["total"] == 4
        assert all_time["window_days"] is None
    await engine.dispose()


@_run
async def test_tenant_isolation():
    """别的学校的数据一个数字都不能进来看板。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        await _seed(db, tenant="other_school")
        m = await grading_metrics.collect(db, PILOT, days=30)
        assert m["items"]["total"] == 0
        assert m["sources"] == [] and m["by_paper"] == []
        assert m["tickets"]["counts"] == {"pending": 0, "claimed": 0, "reviewed": 0}
    await engine.dispose()


@_run
async def test_frozen_manual_is_called_out_separately():
    """冻结导致的转人工要单列：那是护栏在止损，不是解析能力退化。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, sub, items, _, _ = await _seed(db)
        items[2].grading_source = SOURCE_MANUAL_FROZEN
        db.add(
            ExamAnswerFreeze(
                tenant_id=PILOT, content_hash="h3", answer_fingerprint="fp",
                answer="2", flips=3, threshold=3, status="frozen",
            )
        )
        await db.commit()

        m = await grading_metrics.collect(db, PILOT, days=30)
        cov = m["metrics"]["coverage"]
        assert cov["frozen_manual"] == 1
        assert "冻结" in cov["note"]
        assert cov["numerator"] == 2      # 冻结那题没进分子（确实没自动判）
    await engine.dispose()


@_run
async def test_flip_counts_match_guard_semantics():
    """有效改判 = 推翻已有判定且结论真的变了；首次判定与重复保存都不算。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, sub, items, t1, t2 = await _seed(db)
        # t2 已判定为 wrong → 改判为 correct（冤枉：错改对）
        db.add(
            ExamGradingRevision(
                ticket_id=t2.id, submission_item_id=items[3].id, action="rejudge",
                actor_id="t2", before_correct=False, after_correct=True,
            )
        )
        # 重复保存（结论没变）：不算改判
        db.add(
            ExamGradingRevision(
                ticket_id=t2.id, submission_item_id=items[3].id, action="rejudge",
                actor_id="t2", before_correct=True, after_correct=True,
            )
        )
        await db.commit()

        m = await grading_metrics.collect(db, PILOT, days=30)
        assert m["revisions"]["effective_flips"] == 1
        assert m["revisions"]["wrong_to_right"] == 1
        assert m["revisions"]["wrong_to_right_rate"] == 1.0
    await engine.dispose()
