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
            # 题型应来自卷题快照，供错题本按题型筛选
            assert rows[0].question_type == "choice"

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


def test_push_question_type_is_stored_and_returned():
    """学生端收错题带上 question_type，服务端落库并经 wrong_out 回传。"""
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await wrongbook.upsert_sync(db, "student-1", [{
                "client_id": "app:manual-1",
                "question": "手动收的错题",
                "my_answer": "A",
                "correct_answer": "B",
                "knowledge_point": "MA-B1",
                "error_type": "calc",
                "question_type": "choice",
                "source": "app",
                "updated_at": 1700000000000,
            }])
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 1
            assert rows[0].question_type == "choice"
            out = wrongbook.wrong_out(rows[0])
            assert out["question_type"] == "choice"
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- 2026-11-14 后台错题列表 500

def test_wrong_question_schema_allows_null_legacy_columns():
    """POST 直接返回 ORM 对象，标答/作答/知识点为 NULL 时不能 ResponseValidationError。"""
    out = WrongQuestionOut(student_id="s", question="q")
    assert out.correct_answer is None
    assert out.wrong_answer is None
    assert out.knowledge_point is None


def test_enrich_wrong_survives_null_answers_and_carries_qtype_options():
    """线上 46 条未软删错题里 12 条 correct_answer IS NULL，曾让列表整批 500。

    另外题型 / 选项此前没进 enrich dict，pydantic 静默填默认值导致后台恒拿不到。
    """
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
                WrongQuestion(
                    id=1, student_id="student@tes.edu", question="1+1=?",
                    # 三列全 NULL：历史手工录入错题的真实形态
                    wrong_answer=None, correct_answer=None, knowledge_point=None,
                    question_type="choice",
                    options='[{"label":"A","content":"1"}]', source="question",
                ),
                WrongQuestion(
                    id=2, student_id="student@tes.edu", question="脏 options",
                    correct_answer="B", options="{not-json",
                ),
            ])
            await db.commit()

            rows = await ops.list_wrong(db, "student@tes.edu")
            assert len(rows) == 2
            assert len(await ops.list_wrong(db, "student@tes.edu", limit=1)) == 1

            enriched = await ops.enrich_wrong(db, rows)
            outs = [WrongQuestionOut(**item) for item in enriched]
            assert outs[0].correct_answer == ""
            assert outs[0].wrong_answer == ""
            assert outs[0].knowledge_point == ""
            assert outs[0].question_type == "choice"
            assert outs[0].options == [{"label": "A", "content": "1"}]
            # 非法 JSON 退成 None，不能把整个列表拖成 500
            assert outs[1].options is None
        await engine.dispose()

    asyncio.run(scenario())


def test_wrong_list_role_whitelist_matches_menu():
    """region_admin 菜单里有错题本，接口必须放行；school 无菜单仍应 403。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import wrong_questions as wq
    from app.core.db import get_db

    class _EmptyResult:
        def scalars(self): return self
        def all(self): return []
        def scalar_one_or_none(self): return None

    class _EmptyDB:
        async def execute(self, stmt, params=None): return _EmptyResult()

    app = FastAPI()
    app.include_router(wq.router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: _EmptyDB()
    headers = lambda role: {"X-User-Id": "u_test", "X-User-Role": role}
    try:
        with TestClient(app) as client:
            for role in ("admin", "teacher", "parent", "region_admin"):
                r = client.get(
                    "/api/admin/wrong-questions",
                    params={"student_id": "s"}, headers=headers(role),
                )
                assert r.status_code == 200, (role, r.text)
            for role in ("school", "student"):
                r = client.get(
                    "/api/admin/wrong-questions",
                    params={"student_id": "s"}, headers=headers(role),
                )
                assert r.status_code == 403, (role, r.text)
            # limit 有边界，不能让前端一次拉爆全表
            r = client.get(
                "/api/admin/wrong-questions",
                params={"student_id": "s", "limit": 0}, headers=headers("admin"),
            )
            assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
