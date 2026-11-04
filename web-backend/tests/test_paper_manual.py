"""M4-1 手动出卷 / M4-5 版本复用 / M4-6 答案必填 / M4-7 G2 抽样（sqlite 真跑）。"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import ExamAssignment, ExamPaper
from app.models.schemas import (
    AssignmentCreate,
    ExamPaperCreate,
    ManualPaperCreate,
    ManualPaperItem,
)


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _bank(db, *, n=3, **kw):
    for i in range(1, n + 1):
        row = dict(
            subject="数学", grade="高三", type="单选", stem=f"题目 {i}", answer="A",
            options={"A": "1", "B": "2"}, content_hash=f"h-{i}",
            tenant_id="school-1", source_tag="import", status="published",
            knowledge_points=["KP1"], difficulty=3,
        )
        row.update(kw)
        db.add(CourseQuestion(**row))


def _teacher(tenant="school-1"):
    return {"user_id": "teacher-1", "tenant_id": tenant}


def test_manual_paper_saves_scores_and_snapshots():
    """M4-1：5 题配分保存为草稿卷 -> 逐题 score/总分可见，且未发布。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=3)
            await db.commit()
            ids = [r.id for r in (await db.execute(
                __import__("sqlalchemy").select(CourseQuestion)
            )).scalars().all()]
            body = ManualPaperCreate(
                title="手动卷",
                questions=[ManualPaperItem(question_id=ids[0], score=5),
                           ManualPaperItem(question_id=ids[1], score=7)],
            )
            out = await exam.create_manual_paper(body, user=_teacher(), db=db)
            assert out.published is False
            assert out.source == "manual"
            assert out.version == 1
            assert out.total_score == 12
            assert [q.score for q in out.questions] == [5, 7]
            assert [q.question_id for q in out.questions] == ids[:2]
            # 答案预展开（M2-4）
            assert out.questions[0].answer_values
        await engine.dispose()

    asyncio.run(scenario())


def test_manual_paper_rejects_other_tenant_and_draft():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1, tenant_id="other-school")
            _bank(db, n=1, content_hash="draft-1", status="draft")
            await db.commit()
            rows = (await db.execute(__import__("sqlalchemy").select(CourseQuestion))).scalars().all()
            other = [r.id for r in rows if r.tenant_id == "other-school"][0]
            draft = [r.id for r in rows if r.status == "draft"][0]

            with pytest.raises(HTTPException) as exc:
                await exam.create_manual_paper(
                    ManualPaperCreate(questions=[ManualPaperItem(question_id=other)]),
                    user=_teacher(), db=db,
                )
            assert exc.value.status_code == 404

            with pytest.raises(HTTPException) as exc:
                await exam.create_manual_paper(
                    ManualPaperCreate(questions=[ManualPaperItem(question_id=draft)]),
                    user=_teacher(), db=db,
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "QUESTION_NOT_PUBLISHED"
        await engine.dispose()

    asyncio.run(scenario())


def test_manual_paper_rejects_wrong_qtype_for_paper_type():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1, type="填空", answer="3/2")
            await db.commit()
            qid = (await db.execute(__import__("sqlalchemy").select(CourseQuestion))).scalars().first().id
            with pytest.raises(HTTPException) as exc:
                await exam.create_manual_paper(
                    ManualPaperCreate(paper_type="pure_choice",
                                      questions=[ManualPaperItem(question_id=qid)]),
                    user=_teacher(), db=db,
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "QTYPE_NOT_ALLOWED"
        await engine.dispose()

    asyncio.run(scenario())


def test_copy_paper_is_independent_snapshot():
    """M4-5：复制卷是新 id / version=1 / 草稿；改副本不改原卷判据。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=2)
            await db.commit()
            ids = [r.id for r in (await db.execute(__import__("sqlalchemy").select(CourseQuestion))).scalars().all()]
            src = await exam.create_manual_paper(
                ManualPaperCreate(questions=[ManualPaperItem(question_id=i, score=5) for i in ids]),
                user=_teacher(), db=db,
            )
            copy = await exam.copy_paper(src.id, user=_teacher(), db=db)
            assert copy.id != src.id
            assert copy.version == 1 and copy.source == "copy"
            assert copy.published is False
            assert [q.question_id for q in copy.questions] == [q.question_id for q in src.questions]
            # 原卷仍保持草稿/未变答案
            again = await exam.get_paper(src.id, user=_teacher(), db=db)
            assert again.questions[0].answer == "A"
            assert again.version == 2  # 被另存后版本自增
        await engine.dispose()

    asyncio.run(scenario())


def test_copy_paper_rejects_cross_tenant():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1)
            await db.commit()
            qid = (await db.execute(__import__("sqlalchemy").select(CourseQuestion))).scalars().first().id
            src = await exam.create_manual_paper(
                ManualPaperCreate(questions=[ManualPaperItem(question_id=qid)]),
                user=_teacher(), db=db,
            )
            with pytest.raises(HTTPException) as exc:
                await exam.copy_paper(src.id, user=_teacher("other-school"), db=db)
            assert exc.value.status_code == 404
        await engine.dispose()

    asyncio.run(scenario())


def test_manual_paper_without_score_blocks_publish():
    """M4-6：未配分的题发布 -> 409（不替教师猜分）。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1)
            await db.commit()
            qid = (await db.execute(__import__("sqlalchemy").select(CourseQuestion))).scalars().first().id
            paper = await exam.create_manual_paper(
                ManualPaperCreate(questions=[ManualPaperItem(question_id=qid)]),
                user=_teacher(), db=db,
            )
            with pytest.raises(HTTPException) as exc:
                await exam.publish_paper(paper.id, user=_teacher(), db=db)
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "SCORE_MISSING"
        await engine.dispose()

    asyncio.run(scenario())


def test_rule_paper_reports_distribution_and_total_score():
    """M4-2：规则组卷回报实际分布 + 总分；难度配比合计不符直接报参数冲突。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=4)
            await db.commit()
            out = await exam.compose_paper(
                ExamPaperCreate(count=3), user=_teacher(), db=db,
            )
            assert out.question_count == 3
            assert out.source == "rule"
            assert out.total_score == 15
            assert out.compose_report["total"] == 3
            assert out.compose_report["by_difficulty"] == {"3": 3} or out.compose_report["by_difficulty"] == {3: 3}

            with pytest.raises(HTTPException) as exc:
                await exam.compose_paper(
                    ExamPaperCreate(count=3, difficulty_mix={3: 1}),
                    user=_teacher(), db=db,
                )
            assert exc.value.status_code == 409
            assert "难度配比" in exc.value.detail["message"]
        await engine.dispose()

    asyncio.run(scenario())


def test_rule_paper_mentions_missing_kp():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=2, knowledge_points=["KP1"])
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam.compose_paper(
                    ExamPaperCreate(count=2, knowledge_points=["KP1", "KP9"]),
                    user=_teacher(), db=db,
                )
            detail = exc.value.detail
            assert exc.value.status_code == 409
            assert "KP9" in detail["message"]
            assert detail["missing_kps"] == ["KP9"]
        await engine.dispose()

    asyncio.run(scenario())


def test_paper_id_endpoints_all_reject_cross_tenant():
    """B 端按 id 取卷的四个接口统一走 _paper_or_404：别校卷一律 404。

    回归背景：get_paper / validation / publish / assign 曾各自 select(id)，
    只有 copy_paper 走租户助手，别校教师凭 paper_id 就能发布、下发本校卷。
    """
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1)
            await db.commit()
            qid = (await db.execute(select(CourseQuestion))).scalars().first().id
            paper = await exam.create_manual_paper(
                ManualPaperCreate(questions=[ManualPaperItem(question_id=qid, score=5)]),
                user=_teacher(), db=db,
            )
            # 同租户仍可正常读取
            assert (await exam.get_paper(paper.id, user=_teacher(), db=db)).id == paper.id

            other = _teacher("other-school")
            cases = [
                ("get_paper", lambda: exam.get_paper(paper.id, user=other, db=db)),
                ("validation", lambda: exam.validate_paper_for_publish(paper.id, user=other, db=db)),
                ("publish", lambda: exam.publish_paper(paper.id, user=other, db=db)),
                ("assign", lambda: exam.assign_paper(
                    paper.id,
                    AssignmentCreate(target_type="student", target_student="student@tes.edu"),
                    user=other, db=db,
                )),
            ]
            for name, call in cases:
                with pytest.raises(HTTPException) as exc:
                    await call()
                assert exc.value.status_code == 404, f"{name} 应把别校卷当 404"

            # 别校的发布/下发没有产生任何副作用
            assert (await db.execute(select(ExamAssignment))).scalars().all() == []
            assert (await exam.get_paper(paper.id, user=_teacher(), db=db)).published is False
        await engine.dispose()

    asyncio.run(scenario())


def test_legacy_paper_without_tenant_still_readable():
    """迁移前 tenant_id=NULL 的老卷保持可读，不因补租户校验被误伤。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            _bank(db, n=1)
            await db.commit()
            qid = (await db.execute(select(CourseQuestion))).scalars().first().id
            paper = await exam.create_manual_paper(
                ManualPaperCreate(questions=[ManualPaperItem(question_id=qid, score=5)]),
                user=_teacher(), db=db,
            )
            row = (await db.execute(select(ExamPaper))).scalars().first()
            row.tenant_id = None
            await db.commit()

            out = await exam.get_paper(row.id, user=_teacher("other-school"), db=db)
            assert out.id == row.id
        await engine.dispose()

    asyncio.run(scenario())