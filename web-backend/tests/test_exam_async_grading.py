"""M1-7/M1-1 提交两段式判分回归（2026-11-18 补齐卡面子项）。

锁死三件事：
1. 提交先落原始作答（submission=grading / item=pending），判分管线跑完才翻态；
2. 填空逐空明细 blank_results 落库（M1-1 卡面补列）；
3. 补跑分支：grading 半成品 + pending 行 → 重复提交补跑管线，不双写错题/工单。
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.models.course_kb import Base
from app.models.ops import (
    ExamAssignment,
    ExamGradingTicket,
    ExamPaper,
    ExamPaperQuestion,
    ExamSubmission,
    ExamSubmissionItem,
)
from app.models.schemas import SubmitExamRequest, SubmitItem


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


USER = {"user_id": "stu-1", "tenant_id": "school-1", "role": "student"}


async def _seed(db):
    db.add_all([
        ExamPaper(id=1, tenant_id="school-1", title="卷", published=True),
        ExamAssignment(id=2, paper_id=1, tenant_id="school-1",
                       target_type="student", target_student="stu-1"),
        # seq1 选择（答对）；seq2 双空填空（第一空对、第二空错 -> 整题错，多空须全对）
        ExamPaperQuestion(id=11, paper_id=1, seq=1, content_hash="h1",
                          qtype="choice", stem="t1", answer="B", options=["A", "B"]),
        ExamPaperQuestion(id=12, paper_id=1, seq=2, content_hash="h2",
                          qtype="blank", stem="t2", answer="2,3"),
    ])
    await db.commit()


def test_submit_two_phase_persists_raw_answers_then_grades():
    """同步模式（默认开关）：对外结果与旧行为一致，但 item 逐空明细已落库。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await _seed(db)
            req = SubmitExamRequest(answers=[
                SubmitItem(seq=1, student_answer="B"),
                SubmitItem(seq=2, blanks=["2", "9"]),
            ])
            out = await exam.submit_exam(2, req, user=USER, db=db)
            assert out.status == "graded"
            assert out.correct_count == 1
            sub = (await db.execute(select(ExamSubmission))).scalar_one()
            assert sub.status == "graded"
            items = (await db.execute(
                select(ExamSubmissionItem).order_by(ExamSubmissionItem.seq)
            )).scalars().all()
            # 无 pending 残留
            assert all(it.status in ("auto", "manual_pending") for it in items)
            blank_it = items[1]
            # M1-1：逐空明细落库，idx/verdict 可核对
            assert blank_it.blank_results is not None
            verdicts = [d["verdict"] for d in blank_it.blank_results]
            assert verdicts[0] == "correct"
            assert blank_it.correct is False  # 多空须全对
            # blanks 原始作答也落库（异步管线读取源）
            assert blank_it.blanks == ["2", "9"]
        await engine.dispose()

    asyncio.run(scenario())


def test_grading_state_resume_on_duplicate_submit():
    """半成品（grading + pending 行）重复提交 -> 补跑管线，终态且只跑一次。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await _seed(db)
            # 手工构造「第 1 段跑完、第 2 段没跑」的半成品（模拟后台任务丢失）
            sub = ExamSubmission(assignment_id=2, paper_id=1, student_id="stu-1",
                                 tenant_id="school-1", status="grading", total_count=2)
            db.add(sub)
            await db.flush()
            db.add_all([
                ExamSubmissionItem(submission_id=sub.id, paper_question_id=11, seq=1,
                                   qtype="choice", student_answer="B", status="pending"),
                ExamSubmissionItem(submission_id=sub.id, paper_question_id=12, seq=2,
                                   qtype="blank", student_answer="9；9",
                                   blanks=["9", "9"], status="pending"),
            ])
            await db.commit()

            req = SubmitExamRequest(answers=[SubmitItem(seq=1, student_answer="B")])
            out = await exam.submit_exam(2, req, user=USER, db=db)
            assert out.status == "graded"
            assert out.correct_count == 1
            # 再提交一次：已 graded，不重复判定、不重复开单
            out2 = await exam.submit_exam(2, req, user=USER, db=db)
            assert out2.correct_count == 1
            n_items = len((await db.execute(select(ExamSubmissionItem))).scalars().all())
            assert n_items == 2
            assert not (await db.execute(
                select(ExamSubmissionItem).where(ExamSubmissionItem.status == "pending")
            )).scalars().all()
        await engine.dispose()

    asyncio.run(scenario())


def test_async_mode_submit_returns_grading_and_pipeline_idempotent():
    """开关打开：提交即返回 grading 半成品；_run_grading 幂等（重复调用零副作用）。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        from app.core.config import settings
        old = settings.exam_async_grading
        settings.exam_async_grading = True
        try:
            async with factory() as db:
                await _seed(db)
                req = SubmitExamRequest(answers=[
                    SubmitItem(seq=1, student_answer="B"),
                    SubmitItem(seq=2, blanks=["2", "3"]),
                ])
                fired = []
                class _BT:
                    def add_task(self, fn, *a):
                        fired.append((fn, a))
                out = await exam.submit_exam(2, req, background_tasks=_BT(), user=USER, db=db)
                assert out.status == "grading"
                assert out.correct_count == 0
                # 判分任务已排队（BackgroundTasks 语义），未同步执行
                assert fired and fired[0][0] is exam._grade_in_background
                sub = (await db.execute(select(ExamSubmission))).scalar_one()
                assert sub.status == "grading"
                pend = (await db.execute(
                    select(ExamSubmissionItem).where(ExamSubmissionItem.status == "pending")
                )).scalars().all()
                assert len(pend) == 2
                # 管线补跑（模拟后台执行）+ 幂等复跑
                await exam._run_grading(db, sub)
                await db.commit()
                await exam._run_grading(db, sub)
                await db.commit()
                fresh = (await db.execute(select(ExamSubmission))).scalar_one()
                assert fresh.status == "graded"
                assert fresh.correct_count == 2
                tickets = (await db.execute(select(ExamGradingTicket))).scalars().all()
                assert tickets == []
        finally:
            settings.exam_async_grading = old
        await engine.dispose()

    asyncio.run(scenario())
