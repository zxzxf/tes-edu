"""T-CAP4 异常队列 API 单测（B 端处置页的后端契约）。

覆盖：
- ``stats``：隔离/待人工/已归档计数与按闸、按链路、按 tag 分布，released 不计入待办；
- ``items``：kind 三态互斥（archived 不出现在 quarantine）、gate 过滤、关键词过滤、
  重复题附 ``dup_of`` 指向同指纹的另一条；
- ``resolve``：release→draft、publish→published（答案不合规 409）、archive→移出队列但
  保留数据；处置后清空 guard_gate/guard_reason 并留痕 reviewed_by；
- 守卫：已处置再处置 409、跨租户 404（不泄漏别校题目存在性）。
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import quarantine as qapi
from app.models.course_kb import Base, CourseQuestion

TENANT = "school_pilot_01"
OTHER = "school_other_02"
TEACHER = {"user_id": "t_1", "tenant_id": TENANT, "role": "teacher"}

STEM_DUP = "设集合 A={x|-2<x<4}，B={2,3,4,5}，则 A∩B=( )"


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _run(scenario):
    engine = _engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            await scenario(db)
    finally:
        await engine.dispose()


def _q(**over) -> CourseQuestion:
    base = dict(
        subject="数学", grade="高三", type="选择", stem="题干", answer="A",
        options=["A.1", "B.2"], difficulty=3, knowledge_points=["集合"],
        tenant_id=TENANT, status="draft", source_tag="teacher",
        content_hash=f"h{over.get('id', 0)}-{over.get('stem', '')[:6]}",
        created_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return CourseQuestion(**base)


def _stub(db):
    """预置一份典型队列：2 隔离（不同闸）+ 1 打标 + 1 归档 + 1 正常。"""
    rows = [
        _q(stem="越界题", answer="H", status="quarantine",
           source_tag="teacher:guard_quarantine", guard_gate="answer_out_of_range",
           guard_reason="标答 H 不在选项 A/B 内", content_hash="h1"),
        _q(stem=STEM_DUP, status="quarantine",
           source_tag="auto_crawl:guard_quarantine", guard_gate="dedup_text",
           guard_reason="题干文本指纹重复", content_hash="h2"),
        _q(stem="打标题", status="draft",
           source_tag="auto_crawl:guard_manual_review", guard_gate="choice_no_second_source",
           guard_reason="选择题无第二来源", content_hash="h3"),
        _q(stem="归档题", status="quarantine",
           source_tag="import:guard_archived", content_hash="h4"),
        _q(stem="正常题", status="draft", source_tag="teacher", content_hash="h5"),
    ]
    db.add_all(rows)
    return rows


# ---------------------------------------------------------------- stats
def test_stats_counts_and_distribution():
    async def scenario(db):
        _stub(db)
        await db.commit()
        s = await qapi.stats(user=TEACHER, db=db)
        assert s["quarantine_total"] == 2
        assert s["flagged_total"] == 1
        assert s["archived_total"] == 1
        assert s["by_gate"] == {"answer_out_of_range": 1, "dedup_text": 1, "choice_no_second_source": 1}
        assert s["by_chain"] == {"teacher": 1, "crawl": 2}
        assert s["by_source"]["teacher:guard_quarantine"] == 1
        assert s["gate_labels"]["dedup_text"] == "题干指纹重复"

    asyncio.run(_run(scenario))


def test_stats_ignores_released():
    """已放行的题（guard_released）不再计入任何待办计数。"""
    async def scenario(db):
        db.add(_q(stem="放过", status="draft", source_tag="teacher:guard_released", content_hash="r1"))
        await db.commit()
        s = await qapi.stats(user=TEACHER, db=db)
        assert s["quarantine_total"] == 0 and s["flagged_total"] == 0
        assert s["by_gate"] == {}

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- items
def test_items_quarantine_excludes_archived():
    async def scenario(db):
        _stub(db)
        await db.commit()
        rows = await qapi.items(kind="quarantine", gate=None, q=None, limit=50, user=TEACHER, db=db)
        stems = {r["stem"] for r in rows}
        assert stems == {"越界题", STEM_DUP}
        assert all(r["status"] == "quarantine" for r in rows)

        arch = await qapi.items(kind="archived", gate=None, q=None, limit=50, user=TEACHER, db=db)
        assert [r["stem"] for r in arch] == ["归档题"]

        flag = await qapi.items(kind="flagged", gate=None, q=None, limit=50, user=TEACHER, db=db)
        assert [r["stem"] for r in flag] == ["打标题"]
        assert flag[0]["chain"] == "crawl"

    asyncio.run(_run(scenario))


def test_items_gate_and_keyword_filter():
    async def scenario(db):
        _stub(db)
        await db.commit()
        rows = await qapi.items(kind="quarantine", gate="dedup_text", q=None, limit=50,
                                user=TEACHER, db=db)
        assert [r["stem"] for r in rows] == [STEM_DUP]

        hit = await qapi.items(kind="quarantine", gate=None, q="越界", limit=50,
                               user=TEACHER, db=db)
        assert [r["stem"] for r in hit] == ["越界题"]

        none = await qapi.items(kind="quarantine", gate=None, q="不存在的词", limit=50,
                                user=TEACHER, db=db)
        assert none == []

    asyncio.run(_run(scenario))


def test_items_dup_of_points_to_other_row():
    """重复题要告诉人工「和哪一题重复」——判 dedup_text 时按指纹找同库另一条。"""
    async def scenario(db):
        original = _q(stem=STEM_DUP, status="published", source_tag="gaokao_real",
                      content_hash="orig1")
        dup = _q(stem=STEM_DUP.replace("，", ","), status="quarantine",
                 source_tag="auto_crawl:guard_quarantine", guard_gate="dedup_text",
                 guard_reason="指纹重复", content_hash="dup1")
        db.add_all([original, dup])
        await db.commit()

        rows = await qapi.items(kind="quarantine", gate=None, q=None, limit=50,
                                user=TEACHER, db=db)
        assert len(rows) == 1
        assert rows[0]["dup_of"] == original.id

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- resolve
def test_resolve_release_to_draft():
    async def scenario(db):
        row = _stub(db)[0]
        await db.commit()
        out = await qapi.resolve(row.id, qapi.ResolveBody(action="release"), user=TEACHER, db=db)
        assert out["status"] == "draft"
        assert out["source_tag"] == "teacher:guard_released"
        assert out["reviewed_by"] == "t_1"

        fresh = (await db.execute(select(CourseQuestion).where(CourseQuestion.id == row.id))).scalar_one()
        assert fresh.status == "draft"
        # 处置后必须清空隔离原因，否则页面上「已处置」的题还在显示为什么被挡
        assert fresh.guard_gate is None and fresh.guard_reason is None
        assert fresh.reviewed_at is not None

    asyncio.run(_run(scenario))


def test_resolve_publish_requires_acceptable_answer():
    async def scenario(db):
        bad = _q(stem="没答案", answer=None, status="quarantine",
                 source_tag="teacher:guard_quarantine", guard_gate="missing_answer",
                 content_hash="nb1")
        good = _q(stem="答案齐全", answer="3", type="填空", status="quarantine",
                  source_tag="teacher:guard_quarantine", guard_gate="mechanical_mismatch",
                  content_hash="nb2")
        db.add_all([bad, good])
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await qapi.resolve(bad.id, qapi.ResolveBody(action="publish"), user=TEACHER, db=db)
        assert exc.value.status_code == 409

        out = await qapi.resolve(good.id, qapi.ResolveBody(action="publish"), user=TEACHER, db=db)
        assert out["status"] == "published"
        assert out["source_tag"] == "teacher:guard_released"

    asyncio.run(_run(scenario))


def test_resolve_archive_removes_from_queue_but_keeps_row():
    async def scenario(db):
        row = _stub(db)[1]
        await db.commit()
        out = await qapi.resolve(row.id, qapi.ResolveBody(action="archive"), user=TEACHER, db=db)
        assert out["status"] == "quarantine"
        assert out["source_tag"] == "auto_crawl:guard_archived"

        # 数据没删（重复题该删哪条要人工判断，脚本不做硬删除），只是移出待办
        assert (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id == row.id)
        )).scalar_one() is not None
        pending = await qapi.items(kind="quarantine", gate=None, q=None, limit=50,
                                   user=TEACHER, db=db)
        assert row.id not in {r["id"] for r in pending}
        archived = await qapi.items(kind="archived", gate=None, q=None, limit=50,
                                    user=TEACHER, db=db)
        assert row.id in {r["id"] for r in archived}

    asyncio.run(_run(scenario))


def test_resolve_twice_conflicts():
    """已处置的题再处置要 409，不能悄悄改第二次状态。"""
    async def scenario(db):
        row = _stub(db)[0]
        await db.commit()
        await qapi.resolve(row.id, qapi.ResolveBody(action="release"), user=TEACHER, db=db)
        with pytest.raises(HTTPException) as exc:
            await qapi.resolve(row.id, qapi.ResolveBody(action="release"), user=TEACHER, db=db)
        assert exc.value.status_code == 409

    asyncio.run(_run(scenario))


def test_resolve_bad_action_rejected():
    async def scenario(db):
        row = _stub(db)[0]
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await qapi.resolve(row.id, qapi.ResolveBody(action="delete"), user=TEACHER, db=db)
        assert exc.value.status_code == 422

    asyncio.run(_run(scenario))


def test_resolve_cross_tenant_404():
    """别校的题处置不了，且返回 404 而不是 403（不泄漏存在性）。"""
    async def scenario(db):
        row = _q(stem="别校题", tenant_id=OTHER, status="quarantine",
                 source_tag="teacher:guard_quarantine", guard_gate="dedup_exact",
                 content_hash="x1")
        db.add(row)
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await qapi.resolve(row.id, qapi.ResolveBody(action="release"), user=TEACHER, db=db)
        assert exc.value.status_code == 404

    asyncio.run(_run(scenario))


def test_stats_scoped_to_tenant():
    async def scenario(db):
        db.add_all([
            _q(stem="本校", status="quarantine", source_tag="teacher:guard_quarantine",
               guard_gate="dedup_text", content_hash="s1"),
            _q(stem="别校", tenant_id=OTHER, status="quarantine",
               source_tag="teacher:guard_quarantine", guard_gate="dedup_text",
               content_hash="s2"),
        ])
        await db.commit()
        s = await qapi.stats(user=TEACHER, db=db)
        assert s["quarantine_total"] == 1

    asyncio.run(_run(scenario))
