"""M4-3 LLM 辅助组卷（只出草稿） + M4-4 采集卷 P1/P2 两条入库路径。"""
import asyncio
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import CrawlJob, CrawlRun, ExamPaper, ExamPaperQuestion
from app.crawl_service import scheduler as crawl
from app.crawl_service import intake as crawl_intake
from app.services import llm_paper


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _teacher(tenant="school-1"):
    return {"user_id": "teacher-1", "tenant_id": tenant}


# ---------------------------------------------------------------- M4-3 纯函数
def test_normalize_rejects_bad_answer_and_duplicate():
    payload = {"questions": [
        {"type": "单选", "stem": "题1", "options": ["A.1", "B.2"], "answer": "A"},
        {"type": "单选", "stem": "题2", "options": ["A.1", "B.2"], "answer": "见解析"},
        {"type": "单选", "stem": "题1", "options": ["A.1", "B.2"], "answer": "A"},
        {"type": "填空", "stem": "题3", "answer": "3"},
    ]}
    out = llm_paper.normalize_generated(
        payload, subject="数学", grade="高三", paper_type="pure_choice", limit=10
    )
    assert [i["stem"] for i in out["items"]] == ["题1"]
    reasons = " ".join(r["reason"] for r in out["rejected"])
    assert "答案" in reasons and "重复" in reasons and "白名单" in reasons


def test_normalize_limits_to_requested_count():
    payload = {"questions": [
        {"type": "单选", "stem": f"题{i}", "options": ["A.1", "B.2"], "answer": "A"}
        for i in range(5)
    ]}
    out = llm_paper.normalize_generated(
        payload, subject="数学", grade="高三", paper_type="pure_choice", limit=2
    )
    assert len(out["items"]) == 2


def test_parse_llm_json_strips_markdown_fence():
    assert llm_paper.parse_llm_json('```json\n{"questions":[]}\n```') == {"questions": []}
    with pytest.raises(ValueError):
        llm_paper.parse_llm_json("not json")


# ---------------------------------------------------------------- M4-3 落库
def test_llm_generated_questions_are_draft_and_paper_unpublished():
    """生成 3 题 -> 全部 draft/llm_generated；草稿卷 published=False。"""

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            async def fake_transport(system, user):
                return json.dumps({"questions": [
                    {"type": "单选", "stem": f"草稿题{i}", "options": ["A.1", "B.2"],
                     "answer": "A", "difficulty": 3, "knowledge_points": ["等比数列"]}
                    for i in range(3)
                ]})

            out = await llm_paper.generate_drafts(
                db, tenant_id="school-1", topic="等比数列", count=3,
                transport=fake_transport,
            )
            await db.commit()
            assert out["created"] == 3
            assert out["published"] is False
            rows = (await db.execute(select(CourseQuestion))).scalars().all()
            assert all(r.status == "draft" and r.source_tag == "llm_generated" for r in rows)
            paper = (await db.execute(select(ExamPaper))).scalar_one()
            assert paper.published is False and paper.source == "llm_generated"
            assert (await db.execute(select(ExamPaperQuestion))).scalars().all()
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_generate_without_model_raises_runtime_error():
    """租户没配模型 -> 明确报错，不生成模板题。"""

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with pytest.raises(RuntimeError):
                await llm_paper.generate_drafts(
                    db, tenant_id="school-no-model", topic="数列", count=3
                )
            assert (await db.execute(select(CourseQuestion))).scalars().all() == []
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_draft_question_is_excluded_from_rule_compose():
    """M4-3 验收：未审核的 draft 题不能被规则组卷抽到。"""

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            async def fake_transport(system, user):
                return json.dumps({"questions": [
                    {"type": "单选", "stem": "草稿题", "options": ["A.1", "B.2"], "answer": "A"}
                ]})

            await llm_paper.generate_drafts(
                db, tenant_id="school-1", topic="x", count=1, transport=fake_transport
            )
            await db.commit()
            from app.models.schemas import ExamPaperCreate

            with pytest.raises(HTTPException) as exc:
                await exam.compose_paper(
                    ExamPaperCreate(count=1), user=_teacher(), db=db,
                )
            assert exc.value.status_code == 409
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_draft_paper_cannot_publish_before_review():
    """生成的草稿卷发布 -> 409（题目未审核）。"""

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            async def fake_transport(system, user):
                return json.dumps({"questions": [
                    {"type": "单选", "stem": "草稿题", "options": ["A.1", "B.2"], "answer": "A"}
                ]})

            out = await llm_paper.generate_drafts(
                db, tenant_id="school-1", topic="x", count=1, transport=fake_transport
            )
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.publish_paper(out["paper_id"], user=_teacher(), db=db)
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "QUESTION_UNREVIEWED"
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M3-7 审核流
def test_teacher_draft_is_reviewable_then_composable():
    """录题默认 draft 不可组卷；审核为 published 后同一道题可被规则组卷抽到。"""

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(CourseQuestion(
                subject="数学", grade="高三", type="单选", stem="教师新题",
                options=["A.1", "B.2"], answer="A", content_hash="t-1",
                tenant_id="school-1", source_tag="teacher", status="draft",
            ))
            await db.commit()
            from app.models.schemas import ExamPaperCreate

            with pytest.raises(HTTPException) as exc:
                await exam.compose_paper(
                    ExamPaperCreate(count=1), user=_teacher(), db=db
                )
            assert exc.value.status_code == 409

            row = (await db.execute(select(CourseQuestion))).scalar_one()
            await exam.review_question(
                row.id,
                exam.QuestionReviewRequest(status="published"),
                user=_teacher(), db=db,
            )
            out = await exam.compose_paper(
                ExamPaperCreate(count=1), user=_teacher(), db=db
            )
            assert out.question_count == 1
            assert out.questions[0].stem == "教师新题"
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M4-4 P1/P2
def _job(path, **kw):
    base = dict(id=1, tenant_id="school-1", name="job", path=path,
                budget_limit=100, spent=0, status="active")
    base.update(kw)
    return CrawlJob(**base)


def _items(n=2):
    return [
        dict(subject="数学", grade="高三", type="单选", stem=f"采集题{i}",
             options=["A.1", "B.2"], answer="A", content_hash=f"c{i}",
             knowledge_points=["等比数列"])
        for i in range(n)
    ]


def test_p1_creates_unpublished_paper_and_keeps_questions_in_bank():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()
            run = await crawl.run_job(db, job, items=_items(2))
            await db.commit()
            papers = (await db.execute(select(ExamPaper))).scalars().all()
            assert len(papers) == 1
            assert papers[0].source == "auto_crawl" and papers[0].published is False
            rows = (await db.execute(select(CourseQuestion))).scalars().all()
            assert len(rows) == 2
            assert all(r.status == "draft" and r.source_tag == "auto_crawl" for r in rows)
            assert run.paper_id == papers[0].id
            assert (await db.execute(select(ExamPaperQuestion))).scalars().all()
        await engine.dispose()

    asyncio.run(scenario())


def test_p2_only_fills_bank_without_paper():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            run = await crawl.run_job(db, job, items=_items(2))
            await db.commit()
            assert (await db.execute(select(ExamPaper))).scalars().all() == []
            assert len((await db.execute(select(CourseQuestion))).scalars().all()) == 2
            assert run.paper_id is None
        await engine.dispose()

    asyncio.run(scenario())


def test_budget_stops_before_partial_paper():
    """预算 1 -> 只允许 1 题入库，整卷不会出现「建了卷但题只进一半」。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1", budget_limit=1)
            db.add(job)
            await db.commit()
            run = await crawl.run_job(db, job, items=_items(3))
            await db.commit()
            assert run.budget_stop is True
            assert run.inserted == 1
            paper = (await db.execute(select(ExamPaper))).scalar_one()
            qs = (await db.execute(select(ExamPaperQuestion))).scalars().all()
            assert len(qs) == 1
            assert run.paper_id == paper.id
        await engine.dispose()

    asyncio.run(scenario())


def test_intake_rejects_items_without_answer():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            out = await crawl_intake.intake(
                db, job,
                items=[dict(stem="无答案", type="单选", subject="数学", grade="高三")],
            )
            assert out["inserted"] == 0 and out["rejected"] == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_rollback_run_removes_only_draft_auto_crawl():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()
            run = await crawl.run_job(db, job, items=_items(2))
            await db.commit()
            # 教师审核通过其中一题 -> 回退不能删它
            rows = (await db.execute(select(CourseQuestion))).scalars().all()
            rows[0].status = "published"
            await db.commit()

            out = await crawl_intake.rollback_run(
                db, tenant_id="school-1", run_id=run.id
            )
            await db.commit()
            assert out["removed_questions"] == 1
            assert out["removed_papers"] == 1
            left = (await db.execute(select(CourseQuestion))).scalars().all()
            assert [r.status for r in left] == ["published"]
        await engine.dispose()

    asyncio.run(scenario())
