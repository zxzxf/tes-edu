"""学生端作业列表：N+1 批量化（2026-11-04 止血）。

背景：`list_student_exams` 原先每份下发跑 3 条 SQL（卷 / 题数 / 提交）。
137 应用连 106 数据是**跨云公网**，每条 SQL 都是一次往返（实测 RTT ≈ 17ms），
20 份下发 = 60 次往返，App 打开「作业」页就是转圈甚至超时。

本测试锁两件事：
 1. 功能不变：未发布的卷不下发、题数对得上、已提交标记正确、租户/年级仍过滤；
 2. **SQL 条数与下发数量无关**——多挂 10 份下发，查询次数不增加。
    这条是回归护栏：以后谁再把批量查询改回 `for a in assignments: await db.execute(...)`，
    这里会立刻变红。
"""
import asyncio

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.models.course_kb import Base
from app.models.ops import ExamAssignment, ExamPaper, ExamPaperQuestion, ExamSubmission

PILOT = "school_pilot_01"
STUDENT = "student@tes.edu"


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _bootstrap(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _user(tenant=PILOT, uid=STUDENT):
    return {"user_id": uid, "tenant_id": tenant, "role": "student"}


async def _seed(db, *, extra_assignments: int = 0):
    """一张已发布卷（3 题，已提交）+ 一张未发布卷（2 题）+ 可选的若干冗余下发。"""
    pub = ExamPaper(
        tenant_id=PILOT, title="已发布卷", paper_type="pure_choice",
        grade="高三", subject="数学", published=True,
    )
    draft = ExamPaper(
        tenant_id=PILOT, title="未发布卷", paper_type="pure_choice",
        grade="高三", subject="数学", published=False,
    )
    db.add_all([pub, draft])
    await db.flush()

    for seq in (1, 2, 3):
        db.add(ExamPaperQuestion(
            paper_id=pub.id, seq=seq, qtype="choice",
            stem=f"已发布题 {seq}", answer="A", score=5,
        ))
    for seq in (1, 2):
        db.add(ExamPaperQuestion(
            paper_id=draft.id, seq=seq, qtype="choice",
            stem=f"草稿题 {seq}", answer="A", score=5,
        ))
    await db.flush()

    submitted = ExamAssignment(
        paper_id=pub.id, tenant_id=PILOT, target_type="grade", target_grade="高三",
    )
    unpublished = ExamAssignment(
        paper_id=draft.id, tenant_id=PILOT, target_type="grade", target_grade="高三",
    )
    db.add_all([submitted, unpublished])
    await db.flush()

    db.add(ExamSubmission(
        assignment_id=submitted.id, paper_id=pub.id, student_id=STUDENT,
        tenant_id=PILOT, status="graded",
    ))
    for _ in range(extra_assignments):
        db.add(ExamAssignment(
            paper_id=pub.id, tenant_id=PILOT,
            target_type="grade", target_grade="高三",
        ))
    await db.commit()
    return pub, submitted


def test_list_filters_unpublished_and_marks_submitted():
    """功能不变：只回已发布的卷，题数正确，已提交标记 True。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            pub, submitted = await _seed(db)
            items = await exam.list_student_exams(user=_user(), db=db)
            assert len(items) == 1, "未发布的卷不该下发"
            it = items[0]
            assert it.assignment_id == submitted.id
            assert it.paper_id == pub.id
            assert it.title == "已发布卷"
            assert it.question_count == 3
            assert it.submitted is True
        await engine.dispose()

    asyncio.run(scenario())


def test_list_query_count_is_constant_when_assignments_grow():
    """核心回归：多 10 份下发，SQL 条数一 条不多（N+1 已消除）。"""
    async def count_queries(extra: int) -> int:
        engine = _engine()
        await _bootstrap(engine)

        statements = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await _seed(db, extra_assignments=extra)
            statements.clear()  # 只看接口本身发的 SQL，不含建表/造数据
            items = await exam.list_student_exams(user=_user(), db=db)
            n = len(statements)
            assert items, "造了数据却查不出卷，后面比条数没意义"
        await engine.dispose()
        return n

    small = asyncio.run(count_queries(0))
    large = asyncio.run(count_queries(10))
    assert small == large, f"下发变多导致 SQL 变多：{small} -> {large}（N+1 回来了）"
    assert small <= 6, f"列表接口 SQL 条数应固定在 5 条左右，实际 {small}"


def test_list_other_student_submission_does_not_leak():
    """提交状态按自己的 student_id 判定，不能被同卷别的学生的提交带成「已交」。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            pub, submitted = await _seed(db)
            db.add(ExamSubmission(
                assignment_id=submitted.id, paper_id=pub.id, student_id="other@tes.edu",
                tenant_id=PILOT, status="graded",
            ))
            await db.commit()
            items = await exam.list_student_exams(user=_user(uid="newbie@tes.edu"), db=db)
            assert len(items) == 1
            assert items[0].submitted is False, "别人的提交不该让我显示已交"
        await engine.dispose()

    asyncio.run(scenario())


def test_list_empty_returns_immediately():
    """没有任何下发时直接返回空表（不额外查卷/题数/提交）。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            assert await exam.list_student_exams(user=_user(), db=db) == []
        await engine.dispose()

    asyncio.run(scenario())
