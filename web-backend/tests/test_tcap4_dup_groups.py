"""T-CAP4 v5.23：真题重复组人工裁决 + 异常队列批量处置单测。

红线：
- 接口只摆出「哪几条像同一题」，**绝不自动改 published 题**；删/留由人点选；
- keep_one 只归档组内非保留项（保留数据，不硬删、不清组卷池中的保留题）；
- 组内成员按当前用户数据范围重算，前端伪造 id 被拒；
- 批量处置部分成功要逐条回报，不静默吞掉失败。
"""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import quarantine as qapi
from app.core.intake_guard import text_fingerprint
from app.models.course_kb import Base, CourseQuestion, DupGroupDecision

TENANT = "school_pilot_01"
OTHER = "school_other_02"
TEACHER = {"user_id": "t_1", "tenant_id": TENANT, "role": "teacher"}
ADMIN = {"user_id": "a_1", "role": "admin"}
STEM_A = "设集合 A={x|-2<x<4}，B={2,3,4,5}，则 A∩B=( )"
STEM_A2 = "设集合A={x|-2<x<4}，B={2，3，4，5}，则A∩B=（ ）"  # 仅标点/空格差异，同指纹


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
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


def _q(stem, *, fp=None, hash_=None, status="published", tenant=TENANT, tag="gaokao_real"):
    return CourseQuestion(
        subject="数学", grade="高三", type="选择", stem=stem, answer="C",
        options=["A.1", "B.2", "C.{2,3}", "D.{2,3,4}"],
        status=status, source_tag=tag, tenant_id=tenant,
        content_hash=hash_ or f"h-{stem[:8]}", text_fp=fp or text_fingerprint(stem),
        created_at=datetime.now(timezone.utc),
    )


def _seed(db):
    """2 条同指纹真题 + 1 条无关真题 + 1 条别校同题。"""
    a = _q(STEM_A, hash_="orig-1")
    b = _q(STEM_A2, hash_="orig-2")
    c = _q("完全不同的另一道题", hash_="other-1")
    other = _q(STEM_A, hash_="other-tenant", tenant=OTHER)
    db.add_all([a, b, c, other])
    return a, b, c, other


# ---------------------------------------------------------------- 列出重复组
def test_duplicate_groups_lists_only_real_duplicates():
    async def scenario(db):
        a, b, c, other = _seed(db)
        await db.commit()
        groups = await qapi.duplicate_groups(status="published", q=None, limit=50,
                                             user=TEACHER, db=db)
        assert len(groups) == 1
        g = groups[0]
        assert g["group_key"] == f"fp:{text_fingerprint(STEM_A)}"
        assert g["size"] == 2
        assert {m["id"] for m in g["members"]} == {a.id, b.id}

    asyncio.run(_run(scenario))


def test_duplicate_groups_respects_tenant_and_keyword():
    async def scenario(db):
        _seed(db)
        await db.commit()
        # 教师只看到本校那一组
        assert len(await qapi.duplicate_groups(status="published", q=None, limit=50,
                                               user=TEACHER, db=db)) == 1
        # admin 看全库。别校那条和本校两条同指纹，因此合为一组 3 条。
        admin_groups = await qapi.duplicate_groups(status="published", q=None, limit=50,
                                                   user=ADMIN, db=db)
        assert len(admin_groups) == 1 and admin_groups[0]["size"] == 3
        hit = await qapi.duplicate_groups(status="published", q="完全不同", limit=50,
                                          user=TEACHER, db=db)
        assert hit == []

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- 逐组裁决
def test_keep_one_archives_others_and_keeps_published():
    """保留题原封不动留在卷池；其余归档移出，但数据一条不删。"""
    async def scenario(db):
        a, b, _, _ = _seed(db)
        await db.commit()
        key = f"fp:{text_fingerprint(STEM_A)}"
        out = await qapi.resolve_duplicate_group(
            key, qapi.DuplicateGroupResolveBody(action="keep_one", keep_id=a.id),
            user=TEACHER, db=db,
        )
        assert out["archived_ids"] == [b.id]

        fresh_a = (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id == a.id)
        )).scalar_one()
        fresh_b = (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id == b.id)
        )).scalar_one()
        assert fresh_a.status == "published" and fresh_a.source_tag == "gaokao_real"
        assert fresh_b.status == "quarantine"
        assert fresh_b.source_tag.endswith(":guard_archived")
        assert fresh_b.guard_dup_of == a.id
        assert fresh_b.guard_gate == "dup_group_archived"

        decision = (await db.execute(select(DupGroupDecision))).scalar_one()
        assert decision.decision == "keep_one" and decision.keep_id == a.id
        assert decision.reviewed_by == "t_1"

        # 裁决过的组不再出现在待裁列表
        assert await qapi.duplicate_groups(status="published", q=None, limit=50,
                                           user=TEACHER, db=db) == []

    asyncio.run(_run(scenario))


def test_keep_all_touches_no_question():
    """教研确认不是重复：只写留痕，任何题目状态都不改。"""
    async def scenario(db):
        a, b, _, _ = _seed(db)
        await db.commit()
        key = f"fp:{text_fingerprint(STEM_A)}"
        out = await qapi.resolve_duplicate_group(
            key, qapi.DuplicateGroupResolveBody(action="keep_all"),
            user=TEACHER, db=db,
        )
        assert out["archived_ids"] == []
        assert [r.status for r in (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id.in_([a.id, b.id]))
        )).scalars()] == ["published", "published"]
        d = (await db.execute(select(DupGroupDecision))).scalar_one()
        assert d.decision == "keep_all" and d.keep_id is None

    asyncio.run(_run(scenario))


def test_group_resolve_guards():
    async def scenario(db):
        a, b, _, _ = _seed(db)
        await db.commit()
        key = f"fp:{text_fingerprint(STEM_A)}"

        with pytest.raises(HTTPException) as e1:
            await qapi.resolve_duplicate_group(
                key, qapi.DuplicateGroupResolveBody(action="keep_one", keep_id=99999),
                user=TEACHER, db=db,
            )
        assert e1.value.status_code == 422  # keep_id 不在组内

        with pytest.raises(HTTPException) as e2:
            await qapi.resolve_duplicate_group(
                "bogus", qapi.DuplicateGroupResolveBody(action="keep_all"),
                user=TEACHER, db=db,
            )
        assert e2.value.status_code == 422  # group_key 格式

        await qapi.resolve_duplicate_group(
            key, qapi.DuplicateGroupResolveBody(action="keep_all"), user=TEACHER, db=db,
        )
        with pytest.raises(HTTPException) as e3:
            await qapi.resolve_duplicate_group(
                key, qapi.DuplicateGroupResolveBody(action="keep_all"), user=TEACHER, db=db,
            )
        assert e3.value.status_code == 409  # 重复裁决

        with pytest.raises(HTTPException) as e4:
            await qapi.resolve_duplicate_group(
                f"fp:{text_fingerprint('别校题')}",
                qapi.DuplicateGroupResolveBody(action="keep_all"), user=TEACHER, db=db,
            )
        assert e4.value.status_code == 404

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- 批量处置
def _pending(stem, *, tag="teacher:guard_quarantine", answer="C", hash_="p"):
    return CourseQuestion(
        subject="数学", grade="高三", type="选择", stem=stem, answer=answer,
        status="quarantine", source_tag=tag, tenant_id=TENANT, content_hash=hash_,
        guard_gate="answer_out_of_range", created_at=datetime.now(timezone.utc),
    )


def test_batch_resolve_partial_success():
    """一条合格 + 一条别校 + 一条已处置：合格的成功，其余逐条回报原因。"""
    async def scenario(db):
        good = _pending("批量放行A", hash_="b1")
        bad = _pending("批量放行B", hash_="b2")
        cross = _pending("批量放行C", hash_="b3")
        cross.tenant_id = OTHER
        db.add_all([good, bad, cross])
        await db.commit()

        out = await qapi.batch_resolve(
            qapi.BatchResolveBody(ids=[good.id, cross.id, good.id], action="release"),
            user=TEACHER, db=db,
        )
        assert out["succeeded"] == 1  # 重复 id 只处理一次
        assert out["total"] == 2
        assert [o["id"] for o in out["ok"]] == [good.id]
        assert out["skipped"][0]["status"] == 404
        assert (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id == good.id)
        )).scalar_one().status == "draft"
        assert (await db.execute(
            select(CourseQuestion).where(CourseQuestion.id == bad.id)
        )).scalar_one().status == "quarantine"  # 没被误带上

        too_many = qapi.BatchResolveBody(ids=list(range(101)), action="release")
        with pytest.raises(HTTPException) as e:
            await qapi.batch_resolve(too_many, user=TEACHER, db=db)
        assert e.value.status_code == 422

    asyncio.run(_run(scenario))


def test_batch_resolve_reports_already_handled():
    async def scenario(db):
        row = _pending("批量归档", hash_="b4")
        db.add(row)
        await db.commit()
        await qapi.batch_resolve(
            qapi.BatchResolveBody(ids=[row.id], action="release"), user=TEACHER, db=db,
        )
        out = await qapi.batch_resolve(
            qapi.BatchResolveBody(ids=[row.id], action="archive"), user=TEACHER, db=db,
        )
        assert out["succeeded"] == 0
        assert out["skipped"][0]["status"] == 409

    asyncio.run(_run(scenario))
