"""答案双源交叉校验（M2-9 / G1）回归。

三层：
1. **判定规则**（``app/core/answer_verify.py``）：双源一致/冲突、代入检验、
   无依据必须转教师确认；
2. **落库流转**（``app/services/answer_verify.py``）：同源不算第二来源、
   教师确认跨刷新存活、**答案一改确认自动失效**、已发布卷不许改答案；
3. **发布闸门**：没通过的题必须进 violations（发布接口据此 409）。

最值得防的两个回归：
- 把「题库 = 卷面快照」当成双源一致 → G1 变成自己给自己盖章，永远不会拦任何题；
- 教师确认了 A、之后答案被改成 B，确认仍挂着 → 等于给人盖章走空白。
"""
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.answer_verify import (
    MECH_FAIL,
    MECH_NA,
    MECH_OK,
    STATUS_CONFLICT,
    STATUS_MECHANICAL,
    STATUS_TEACHER,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    answer_equivalent,
    evaluate,
    gate_message,
    is_passing,
    mechanical_check,
    status_label,
    substitution_check,
    summarize,
)
from app.models import ops as _ops  # noqa: F401  注册 exam 域表
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import ExamAnswerCheck, ExamPaper, ExamPaperQuestion
from app.services import answer_verify as verify

PILOT = "school_pilot_01"


def _run(fn):
    def wrapper():
        asyncio.run(fn())

    return wrapper


# ------------------------------------------------------------------ 判定规则
def test_answer_equivalent_follows_qtype():
    # 选择题按标签集合，多选顺序无关
    assert answer_equivalent("choice", "ACD", "d,c,a") is True
    assert answer_equivalent("choice", "AC", "AD") is False
    # 填空走 equiv 三态：数值等价算一致，符号不可判不算一致
    assert answer_equivalent("blank", "3", "3.0") is True
    assert answer_equivalent("blank", "1/2", "0.5") is True
    assert answer_equivalent("blank", "x=1", "x=2") is False


def test_substitution_confirms_correct_root():
    r = substitution_check("已知 x^2-2x-3=0，求 x 的正根", "3")
    assert r["applicable"] is True and r["passed"] is True
    assert r["code"] == MECH_OK


def test_substitution_flags_wrong_root():
    r = substitution_check("已知 x^2-2x-3=0，求 x 的正根", "2")
    assert r["applicable"] is True and r["passed"] is False
    assert r["code"] == MECH_FAIL


def test_substitution_handles_exponent_equation():
    # 2^n=8 代入 n=3 成立
    assert substitution_check("已知 2^n=8，求 n", "3")["passed"] is True


def test_substitution_skips_parameter_assignment():
    """``a=2`` 是参数赋值，不是待解方程 —— 拿它验证 a+b 会把好题判成可疑。"""
    r = substitution_check("已知 a=2，b=3，求 a+b", "5")
    assert r["applicable"] is False
    assert r["code"] == MECH_NA


def test_substitution_skips_two_variable_equation():
    r = substitution_check("已知 3x+4y-5=0，求 x+y 的最小值", "1")
    assert r["applicable"] is False


def test_substitution_skips_function_notation_and_symbolic_answer():
    assert substitution_check("已知 f(x)=x^2+1，求 f(2)", "5")["applicable"] is False
    # 符号答案无法数值化 → 不适用（不是「不成立」）
    assert substitution_check("已知 x^2=2，求 x", "sqrt(2)")["applicable"] is True
    assert substitution_check("已知 x^2=2，求 x", "x=根号2")["applicable"] is False


def test_mechanical_check_only_for_blank():
    assert mechanical_check("choice", "A", {"A": "1"})["applicable"] is False
    assert mechanical_check("subjective", "略")["applicable"] is False


def test_two_independent_sources_agree_is_verified():
    r = evaluate("blank", "3", secondary={"source": "kb", "answer": "3.0"})
    assert r["status"] == STATUS_VERIFIED
    assert r["secondary_source"] == "kb"


def test_two_independent_sources_disagree_is_conflict():
    r = evaluate("choice", "A", multi=False, secondary={"source": "kb", "answer": "B"})
    assert r["status"] == STATUS_CONFLICT
    assert r["suspect"] is True
    assert "不一致" in r["detail"]


def test_no_secondary_and_no_substitution_needs_teacher():
    r = evaluate("choice", "A", options={"A": "1", "B": "2"})
    assert r["status"] == STATUS_UNVERIFIED
    assert r["suspect"] is False


def test_mechanical_pass_is_a_passing_status():
    r = evaluate("blank", "3", stem="已知 x^2-2x-3=0")
    assert r["status"] == STATUS_MECHANICAL
    assert is_passing(r["status"]) is True


def test_suspect_but_not_passing_when_substitution_fails():
    r = evaluate("blank", "2", stem="已知 x^2-2x-3=0")
    assert r["status"] == STATUS_UNVERIFIED
    assert r["suspect"] is True
    assert is_passing(r["status"]) is False


def test_summarize_and_labels():
    rows = [
        {"status": STATUS_VERIFIED},
        {"status": STATUS_MECHANICAL},
        {"status": STATUS_TEACHER},
        {"status": STATUS_CONFLICT},
        {"status": STATUS_UNVERIFIED},
    ]
    s = summarize(rows)
    assert (s["total"], s["passed"], s["conflict"], s["unverified"]) == (5, 3, 1, 1)
    assert s["ok"] is False
    assert summarize([])["ok"] is False        # 空卷不算通过
    assert status_label(STATUS_CONFLICT) == "双源冲突"
    assert gate_message(STATUS_UNVERIFIED, False, "") != ""


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


async def _seed(db: AsyncSession, *, published: bool = False):
    """一道填空题 + 题库同题（answer 与卷面一致，即『同源』）。"""
    p = ExamPaper(tenant_id=PILOT, title="G1 测试卷", paper_type="pure_blank", published=published)
    db.add(p)
    await db.flush()
    q = ExamPaperQuestion(
        paper_id=p.id, seq=1, content_hash="hash-g1", qtype="blank", multi=False,
        stem="已知 x^2-2x-3=0，求 x 的正根", answer="3", answer_values=["3"],
    )
    db.add(q)
    db.add(
        CourseQuestion(
            subject="数学", grade="高三", type="填空", stem=q.stem,
            content_hash="hash-g1", answer="3", tenant_id=PILOT,
        )
    )
    await db.commit()
    await db.refresh(p)
    await db.refresh(q)
    return p, q


@_run
async def test_same_origin_is_not_a_second_source():
    """刚组完卷时题库 = 快照，这是同源，不能算双源一致（否则 G1 永远不拦）。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        rows = await verify.run_checks(db, p, [q])
        assert rows[0].secondary_source is None
        # 该题题干可代入检验，于是走机械档；关键是**不能**是 verified
        assert rows[0].status == STATUS_MECHANICAL


@_run
async def test_kb_divergence_becomes_conflict():
    """题库被改过 → 两个来源真的不同了，比对才有意义。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        kb = (
            await db.execute(
                CourseQuestion.__table__.select().where(CourseQuestion.content_hash == "hash-g1")
            )
        ).first()
        await db.execute(
            CourseQuestion.__table__.update()
            .where(CourseQuestion.id == kb.id)
            .values(answer="5")
        )
        await db.commit()
        rows = await verify.run_checks(db, p, [q])
        assert rows[0].secondary_source == "kb"
        assert rows[0].status == STATUS_CONFLICT
        assert rows[0].secondary_answer == "5"


@_run
async def test_run_checks_is_idempotent():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        first = await verify.run_checks(db, p, [q])
        second = await verify.run_checks(db, p, [q])
        assert first[0].id == second[0].id
        rows = (
            await db.execute(ExamAnswerCheck.__table__.select().where(ExamAnswerCheck.paper_id == p.id))
        ).all()
        assert len(rows) == 1


@_run
async def test_teacher_confirm_survives_rerun_and_dies_with_answer_change():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        # 无第二来源、题干不可代入 → 必须教师确认
        await db.execute(
            ExamPaperQuestion.__table__.update()
            .where(ExamPaperQuestion.id == q.id)
            .values(stem="求下列各式的值", answer="7")
        )
        await db.execute(
            CourseQuestion.__table__.update()
            .where(CourseQuestion.content_hash == "hash-g1")
            .values(answer="7")
        )
        await db.commit()
        await db.refresh(q)

        rows = await verify.run_checks(db, p, [q])
        assert rows[0].status == STATUS_UNVERIFIED

        rows = await verify.confirm(db, p, [q], actor="t1", comment="我核过原卷")
        assert rows[0].status == STATUS_TEACHER and rows[0].confirmed_by == "t1"

        # 重跑校验不该抹掉教师的工作
        rows = await verify.run_checks(db, p, [q])
        assert rows[0].status == STATUS_TEACHER

        # 答案被改了 → 确认失效（否则等于盖章走空白）；此时题库仍是 7，故为冲突
        await db.execute(
            ExamPaperQuestion.__table__.update().where(ExamPaperQuestion.id == q.id).values(answer="8")
        )
        await db.commit()
        await db.refresh(q)
        rows = await verify.run_checks(db, p, [q])
        assert rows[0].status == STATUS_CONFLICT
        assert rows[0].confirmed_by is None


@_run
async def test_confirm_can_fix_answer_and_rewrite_values():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        rows = await verify.confirm(db, p, [q], actor="t1", answers={1: "0.5"})
        await db.refresh(q)
        assert rows[0].status == STATUS_TEACHER
        assert q.answer == "0.5"
        assert q.answer_values == ["0.5"]


@_run
async def test_published_paper_answer_is_frozen():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db, published=True)
        try:
            await verify.confirm(db, p, [q], actor="t1", answers={1: "0.5"})
        except ValueError as exc:
            assert "已发布" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("已发布试卷不应允许改答案")


@_run
async def test_gate_lists_only_unpassing_questions():
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        rows, bad = await verify.gate(db, p, [q])
        # 可代入 → 通过，闸门放行
        assert bad == []
        # 改成不可验证的题 → 必须拦
        await db.execute(
            ExamPaperQuestion.__table__.update()
            .where(ExamPaperQuestion.id == q.id)
            .values(stem="求下列各式的值")
        )
        await db.execute(
            CourseQuestion.__table__.update()
            .where(CourseQuestion.content_hash == "hash-g1")
            .values(answer="3")
        )
        await db.commit()
        await db.refresh(q)
        rows, bad = await verify.gate(db, p, [q])
        assert len(bad) == 1
        assert bad[0]["code"] == "ANSWER_UNVERIFIED"
        assert bad[0]["seq"] == 1


@_run
async def test_cross_tenant_kb_is_not_used_as_second_source():
    """跨租户的题库答案不能拿来做第二来源（否则等于用别校的答案判本校的卷）。"""
    engine, sf = await _bootstrap()
    async with sf() as db:
        p, q = await _seed(db)
        await db.execute(
            CourseQuestion.__table__.update()
            .where(CourseQuestion.content_hash == "hash-g1")
            .values(answer="5", tenant_id="another_school")
        )
        await db.commit()
        rows = await verify.run_checks(db, p, [q])
        assert rows[0].secondary_source is None
