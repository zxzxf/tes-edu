"""Only reviewed, tenant-owned questions can enter a new paper."""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import ExamPaper, ExamPaperQuestion
from app.models.schemas import ExamPaperCreate


def test_draft_and_quarantined_questions_are_excluded_from_composition():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            for number, status in enumerate(("published", "draft", "quarantine"), 1):
                db.add(CourseQuestion(
                    subject="数学", grade="高三", type="单选", stem=f"题目 {number}",
                    answer="A", content_hash=f"hash-{number}", tenant_id="school-1",
                    source_tag="import", status=status, embedding=[0.0] * 1024,
                ))
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.compose_paper(
                    ExamPaperCreate(count=2), user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
                )
            assert exc.value.status_code == 409
            assert "仅 1 道" in exc.value.detail
        await engine.dispose()

    asyncio.run(scenario())


def test_question_review_requires_same_tenant():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            q = CourseQuestion(
                subject="数学", grade="高三", type="单选", stem="待审核", answer="A",
                content_hash="review-hash", tenant_id="school-1", status="draft",
            )
            db.add(q)
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.review_question(
                    q.id, exam.QuestionReviewRequest(status="published"),
                    user={"user_id": "teacher-2", "tenant_id": "school-2"}, db=db,
                )
            assert exc.value.status_code == 404
            assert q.status == "draft"
            reviewed = await exam.review_question(
                q.id, exam.QuestionReviewRequest(status="published"),
                user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
            )
            assert reviewed["status"] == "published"
        await engine.dispose()

    asyncio.run(scenario())


def test_draft_question_blocks_publication_after_paper_was_composed():
    async def scenario():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            kb = CourseQuestion(
                subject="数学", grade="高三", type="单选", stem="待审题", answer="A",
                content_hash="draft-hash", tenant_id="school-1", status="draft",
            )
            paper = ExamPaper(
                title="草稿卷", paper_type="pure_choice", grade="高三", subject="数学",
                tenant_id="school-1", published=False,
            )
            db.add_all([kb, paper])
            await db.flush()
            db.add(ExamPaperQuestion(
                paper_id=paper.id, seq=1, content_hash="draft-hash", qtype="choice",
                stem="待审题", answer="A", options=["1", "2", "3", "4"],
            ))
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.publish_paper(
                    paper.id, user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "QUESTION_UNREVIEWED"
            assert paper.published is False
        await engine.dispose()

    asyncio.run(scenario())


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    return engine


def test_unknown_question_type_with_answer_can_be_published():
    """未知题型（如「实验」）不得兜底成选择题，否则「见解析」类答案永远过不了审核。"""

    async def scenario():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            q = CourseQuestion(
                subject="物理", grade="高三", type="实验", stem="测定电源电动势",
                answer="(1) 见解析（证明射程与 q、m 无关）",
                content_hash="exp-hash", tenant_id="school-1", status="draft",
            )
            db.add(q)
            await db.commit()
            out = await exam.review_question(
                q.id, exam.QuestionReviewRequest(status="published"),
                user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
            )
            assert out["status"] == "published"
            assert out["reviewed_by"] == "teacher-1"
        await engine.dispose()

    asyncio.run(scenario())


def test_unknown_question_type_without_answer_is_rejected():
    """未知题型放行只放宽题型判定，答案必须有内容。"""

    async def scenario():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            q = CourseQuestion(
                subject="物理", grade="高三", type="实验", stem="空答案实验题",
                answer="  ", content_hash="exp-empty-hash", tenant_id="school-1",
                status="draft",
            )
            db.add(q)
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.review_question(
                    q.id, exam.QuestionReviewRequest(status="published"),
                    user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
                )
            assert exc.value.status_code == 409
            assert q.status == "draft"
        await engine.dispose()

    asyncio.run(scenario())


def test_choice_question_with_non_option_answer_is_rejected():
    """选择题仍须 A-D 合规：不能因为放宽未知题型而放过脏答案。"""

    async def scenario():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            q = CourseQuestion(
                subject="数学", grade="高三", type="单选", stem="选项题脏答案",
                answer="见解析", content_hash="choice-bad-hash", tenant_id="school-1",
                status="draft",
            )
            db.add(q)
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.review_question(
                    q.id, exam.QuestionReviewRequest(status="published"),
                    user={"user_id": "teacher-1", "tenant_id": "school-1"}, db=db,
                )
            assert exc.value.status_code == 409
            assert q.status == "draft"
        await engine.dispose()

    asyncio.run(scenario())
