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
            # T-CAP4：采集题入库即校验，无第二来源的选择题打 guard_manual_review 标
            # （保留 auto_crawl 前缀以兼容回退），status 仍是 draft。
            assert all(
                r.status == "draft" and r.source_tag.startswith("auto_crawl") for r in rows
            )
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


# ---------------------------------------------------------------- T-CAP4 入库即校验 hook
def test_guard_quarantines_answer_out_of_range():
    """采集题答案越界（H 不在 A/B）→ 自动 quarantine，不进正常 draft 流。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            items = [dict(subject="数学", grade="高三", type="选择", stem="单选越界题",
                          options=["A.1", "B.2"], answer="H", content_hash="bad1")]
            out = await crawl_intake.intake(db, job, items=items)
            await db.commit()
            rows = (await db.execute(select(CourseQuestion))).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "quarantine"
            assert rows[0].source_tag == "auto_crawl:guard_quarantine"
            assert out["guard"]["quarantine"] == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_intake_dedups_text_variant_before_guard():
    """题干格式差异但实质相同的采集题 → 入库前按 text_fp 挡住，不制造隔离题。

    dedup_text 闸自身仍由 test_tcap4_intake_guard.py 覆盖；采集链路提前去重，
    避免每天重复采集都积累一批「已知重复」的待裁决脏题。
    """
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            # 先入一道存量题
            db.add(CourseQuestion(
                subject="数学", grade="高三", type="选择",
                stem="设集合 A={x|-2<x<4}, B={2,3,4,5}, 则 A∩B=( )",
                options=["A.1", "B.2"], answer="B", content_hash="exist1",
                tenant_id="school-1", source_tag="gaokao_real", status="published",
            ))
            await db.commit()
            job = _job("P2")
            db.add(job)
            await db.commit()
            # 采集一道仅标点/全角不同的同题，content_hash 不同
            items = [dict(subject="数学", grade="高三", type="选择",
                          stem="设集合 A={x|-2<x<4}，B={2,3,4,5}，则 A∩B=( )",
                          options=["A.1", "B.2"], answer="B", content_hash="new1")]
            out = await crawl_intake.intake(db, job, items=items)
            await db.commit()
            rows = (await db.execute(
                select(CourseQuestion).where(CourseQuestion.content_hash == "new1")
            )).scalars().all()
            assert rows == []
            assert out["inserted"] == 0 and out["duplicated"] == 1
            assert out["paper_id"] is None
        await engine.dispose()

    asyncio.run(scenario())


def test_guard_manual_tag_on_valid_choice():
    """合法但无第二来源的选择题 → manual 打标不隔离（status 仍 draft）。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            out = await crawl_intake.intake(db, job, items=_items(1))
            await db.commit()
            row = (await db.execute(select(CourseQuestion))).scalar_one()
            assert row.status == "draft"
            assert row.source_tag == "auto_crawl:guard_manual_review"
            assert out["guard"]["manual_review"] == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_rollback_removes_guard_quarantined():
    """guard 隔离的采集题也能被 rollback_run 清除（保留 auto_crawl 前缀的意义）。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            items = [dict(subject="数学", grade="高三", type="选择", stem="越界待回退",
                          options=["A.1", "B.2"], answer="H", content_hash="bad2")]
            out = await crawl_intake.intake(db, job, items=items, run_id=99)
            await db.commit()
            assert (await db.execute(select(CourseQuestion))).scalars().all()[0].status == "quarantine"

            res = await crawl_intake.rollback_run(db, tenant_id="school-1", run_id=99)
            await db.commit()
            assert res["removed_questions"] == 1
            assert (await db.execute(select(CourseQuestion))).scalars().all() == []
        await engine.dispose()

    asyncio.run(scenario())


def test_guard_disabled_keeps_legacy(monkeypatch):
    """关掉 guard 后行为回到 legacy：auto_crawl / draft，不写 guard 统计。"""
    async def scenario():
        monkeypatch.setattr(crawl_intake, "GUARD_ENABLED", False)
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P2")
            db.add(job)
            await db.commit()
            out = await crawl_intake.intake(db, job, items=_items(1))
            await db.commit()
            row = (await db.execute(select(CourseQuestion))).scalar_one()
            assert row.status == "draft" and row.source_tag == "auto_crawl"
            assert out["guard"]["enabled"] is False
        await engine.dispose()

    asyncio.run(scenario())


def test_p1_all_duplicated_creates_no_empty_paper():
    """P1 重复采到同一批题：去重后 0 新题时不能再建一张空卷。

    否则定时任务每天抓同一源，题库一条没涨、卷子天天多一张，
    教研还要逐张处置这些空卷。建卷条件本身就带 ``and created``，
    这条测试把它锁死，防止后续改动回归。
    """
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()

            first = await crawl.run_job(db, job, items=_items(2))
            await db.commit()
            assert first.inserted == 2 and first.paper_id is not None

            second = await crawl.run_job(db, job, items=_items(2))
            await db.commit()
            assert second.inserted == 0
            assert second.duplicated == 2
            assert second.paper_id is None

            papers = (await db.execute(select(ExamPaper))).scalars().all()
            assert len(papers) == 1  # 只有第一次那张卷，没有新增空卷
            assert len((await db.execute(select(ExamPaperQuestion))).scalars().all()) == 2
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_trailing_backslash_does_not_defeat_dedup():
    """同题两次抽取只差尾部 LaTeX 反斜杠：hash 不同也要靠 text_fp 挡住。

    真实验收中第二天的 LLM 抽取在每题尾部多出 '\\\\'，content_hash 全部漂移，
    20 道题重复入库还多建了一张卷。这条测试锁住「入库前按 text_fp 去重」。
    """
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()
            first = dict(subject="数学", grade="高三", type="填空",
                         stem="已知函数 f(x)=a x^2，求 f(1)", answer="a",
                         content_hash="hash-round1")
            second = dict(first, stem=first["stem"] + "\\\\",
                          content_hash="hash-round2")
            r1 = await crawl.run_job(db, job, items=[first])
            await db.commit()
            assert r1.inserted == 1

            r2 = await crawl.run_job(db, job, items=[second])
            await db.commit()
            assert r2.inserted == 0 and r2.duplicated == 1
            assert r2.paper_id is None
            assert len((await db.execute(select(ExamPaper))).scalars().all()) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_p1_quarantined_items_never_enter_paper():
    """三闸隔离的题不该混进草稿卷；整批隔离时不建卷。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()
            bad = dict(subject="数学", grade="高三", type="单选", stem="答案越界题",
                       options=["A.1", "B.2"], answer="H", content_hash="bad-answer")
            run = await crawl.run_job(db, job, items=_items(1) + [bad])
            await db.commit()
            assert run.inserted == 2

            rows = (await db.execute(select(ExamPaperQuestion))).scalars().all()
            assert len(rows) == 1 and rows[0].stem == "采集题0"
            blocked = (await db.execute(
                select(CourseQuestion).where(CourseQuestion.content_hash == "bad-answer")
            )).scalar_one()
            assert blocked.status == "quarantine"
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_paper_name_does_not_overflow_paper_type_column():
    """LLM 把试卷全名塞进 paper_type 时归一到 mixed，不让整批采集事务回滚。"""
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job("P1")
            db.add(job)
            await db.commit()
            items = _items(1)
            items[0]["paper_type"] = "2018年数学试卷（理科）（新课标ⅰ）"
            await crawl.run_job(db, job, items=items)
            await db.commit()
            paper = (await db.execute(select(ExamPaper))).scalars().one()
            assert paper.paper_type == "mixed"
            assert len(paper.paper_type) <= 16
        await engine.dispose()

    asyncio.run(scenario())
