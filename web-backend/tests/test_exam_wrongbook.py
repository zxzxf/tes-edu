"""Exam results and teacher rejudgments keep the student wrongbook in sync."""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.course_kb import Base
from app.models.ops import ExamPaperQuestion, ExamSubmissionItem, WrongQuestion
from app.models.ops import ExamAssignment, ExamGradingTicket, ExamPaper, ExamSubmission
from app.models.schemas import GradingJudgeRequest, SubmitExamRequest, SubmitItem
from app.models.schemas import WrongQuestionOut
from app.api import exam, grading
from app.services import wrongbook
from app.services import ops


def test_exam_wrong_question_is_idempotent_and_rejudgment_soft_deletes():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            question = ExamPaperQuestion(
                id=11, paper_id=2, seq=1, content_hash="exam-hash", qtype="choice",
                stem="1 + 1 = ?", answer="B", knowledge_points=["加法"],
            )
            item = ExamSubmissionItem(
                id=21, submission_id=3, paper_question_id=11, seq=1,
                qtype="choice", student_answer="A", correct=False, status="auto",
            )
            db.add_all([question, item])
            await db.commit()

            await wrongbook.sync_exam_wrong_question(db, "student-1", item, question)
            await wrongbook.sync_exam_wrong_question(db, "student-1", item, question)
            await db.commit()
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 1
            assert rows[0].source == "exam"
            assert rows[0].client_id == "exam:21"
            assert rows[0].correct_answer == "B"
            assert rows[0].deleted_at is None

            item.correct = True
            await wrongbook.sync_exam_wrong_question(db, "student-1", item, question)
            await db.commit()
            assert rows[0].deleted_at is not None
            assert await ops.list_wrong(db, "student-1") == []

            item.correct = False
            await wrongbook.sync_exam_wrong_question(db, "student-1", item, question)
            await db.commit()
            assert rows[0].deleted_at is None
            assert len((await db.execute(select(WrongQuestion))).scalars().all()) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_student_sync_cannot_overwrite_server_owned_exam_wrong_question():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            row = WrongQuestion(
                student_id="student-1", client_id="exam:21", source="exam",
                question="原题", wrong_answer="A",
            )
            db.add(row)
            await db.commit()
            await wrongbook.upsert_sync(db, "student-1", [{
                "id": row.id, "question": "伪造题", "my_answer": "B", "deleted": True,
            }])
            assert row.question == "原题"
            assert row.deleted_at is None
        await engine.dispose()

    asyncio.run(scenario())


def test_pending_exam_answer_does_not_enter_wrongbook():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            q = ExamPaperQuestion(id=11, paper_id=2, seq=1, qtype="blank", stem="x?", answer="2")
            item = ExamSubmissionItem(
                id=21, submission_id=3, paper_question_id=11, seq=1,
                qtype="blank", student_answer="x", correct=None, status="manual_pending",
            )
            db.add_all([q, item])
            await db.commit()
            await wrongbook.sync_exam_wrong_question(db, "student-1", item, q)
            await db.commit()
            assert (await db.execute(select(WrongQuestion))).scalars().all() == []
        await engine.dispose()

    asyncio.run(scenario())


def test_sync_exam_wrong_questions_batch_matches_semantics():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            questions = [
                ExamPaperQuestion(
                    id=n, paper_id=1, seq=n, content_hash=f"h{n}", qtype="choice",
                    stem=f"题 {n}", answer="B", knowledge_points=[f"知识点{n}"],
                ) for n in (1, 2, 3)
            ]
            items = [
                ExamSubmissionItem(
                    id=11, submission_id=1, paper_question_id=1, seq=1,
                    qtype="choice", student_answer="A", correct=False, status="auto",
                ),
                ExamSubmissionItem(
                    id=12, submission_id=1, paper_question_id=2, seq=2,
                    qtype="choice", student_answer="B", correct=True, status="auto",
                ),
                ExamSubmissionItem(
                    id=13, submission_id=1, paper_question_id=3, seq=3,
                    qtype="choice", student_answer="C", correct=False, status="auto",
                ),
            ]
            db.add_all([*questions, *items])
            await db.commit()
            q_by_id = {q.id: q for q in questions}
            pairs = [(it, q_by_id[it.paper_question_id]) for it in items]
            # 批量接口：判错的 11/13 入库，判对的 12 无历史行 → 不建行（与逐题版一致）
            await wrongbook.sync_exam_wrong_questions(db, "student-1", pairs)
            await db.commit()
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 2
            assert {r.client_id for r in rows} == {"exam:11", "exam:13"}
            for r in rows:
                assert r.deleted_at is None and r.source == "exam"
            # 幂等：再跑一次行数不变
            await wrongbook.sync_exam_wrong_questions(db, "student-1", pairs)
            await db.commit()
            assert len((await db.execute(select(WrongQuestion))).scalars().all()) == 2
        await engine.dispose()

    asyncio.run(scenario())


def test_exam_submission_writes_wrong_item_once():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper = ExamPaper(id=1, tenant_id="school-1", title="测试卷", published=True)
            assignment = ExamAssignment(
                id=2, paper_id=1, tenant_id="school-1", target_type="student",
                target_student="student-1",
            )
            questions = [
                ExamPaperQuestion(
                    id=n, paper_id=1, seq=n, content_hash=f"h{n}", qtype="choice",
                    stem=f"题 {n}", answer="B", options=["A", "B", "C", "D"],
                ) for n in (1, 2)
            ]
            db.add_all([paper, assignment, *questions])
            await db.commit()
            request = SubmitExamRequest(answers=[
                SubmitItem(seq=1, student_answer="A"),
                SubmitItem(seq=2, student_answer="B"),
            ])
            user = {"user_id": "student-1", "tenant_id": "school-1", "role": "student"}
            await exam.submit_exam(2, request, user=user, db=db)
            await exam.submit_exam(2, request, user=user, db=db)
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 1
            assert rows[0].source == "exam" and rows[0].question == "题 1"
        await engine.dispose()

    asyncio.run(scenario())


def test_teacher_rejudgment_updates_exam_wrongbook():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all([
                ExamPaper(id=1, tenant_id="school-1", title="人工卷"),
                ExamPaperQuestion(
                    id=2, paper_id=1, seq=1, qtype="subjective", stem="计算 1+1",
                    answer="2", content_hash="manual-hash",
                ),
                ExamSubmission(
                    id=3, assignment_id=4, paper_id=1, student_id="student-1",
                    tenant_id="school-1", status="manual_pending", total_count=1,
                ),
                ExamSubmissionItem(
                    id=5, submission_id=3, paper_question_id=2, seq=1,
                    qtype="subjective", student_answer="1", correct=None,
                    status="manual_pending",
                ),
                ExamGradingTicket(
                    id=6, tenant_id="school-1", submission_id=3, submission_item_id=5,
                    assignment_id=4, paper_id=1, paper_question_id=2,
                    student_id="student-1", seq=1, qtype="subjective", status="pending",
                    student_answer="1",
                ),
            ])
            await db.commit()
            user = {"user_id": "teacher-1", "tenant_id": "school-1", "role": "teacher"}
            await grading.judge_ticket(6, GradingJudgeRequest(verdict="wrong"), user=user, db=db)
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 1 and rows[0].deleted_at is None
            visible = (await ops.enrich_wrong(db, await ops.list_wrong(db, "student-1")))[0]
            assert WrongQuestionOut(**visible).source == "exam"
            await grading.judge_ticket(6, GradingJudgeRequest(verdict="correct"), user=user, db=db)
            assert rows[0].deleted_at is not None
            assert await ops.list_wrong(db, "student-1") == []
        await engine.dispose()

    asyncio.run(scenario())
