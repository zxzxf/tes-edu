"""0022 合规审核队列：真实生产者 + 租户隔离 + 幂等 + 审核留痕。

线上排查结论（2026-11-14）：合规审核页一直空，不是接口坏，而是
compliance_items 0 行、没有任何业务链路会写它。这里锁住改造后的行为，
防止「接通生产者」这件事以后又被改回去。
"""
import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.course_kb import Base, QuestionCreate
from app.models.ops import ComplianceItem
from app.services import compliance_queue, ops, question_entry
from app.crawl_service import intake as intake_svc

TENANT = "school_pilot_01"
OTHER = "demo"

FAKE_CFG = {
    "source": "tenant",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-test",
    "model": "qwen3.7-text-embedding",
}


class _FakeResp:
    status_code = 200

    def json(self):
        return {"data": [{"index": 0, "embedding": [0.01] * 1024}]}


async def _exploding_transport(*_a, **_kw):
    raise RuntimeError("no network in unit test")


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


def _count(rows, tenant=None):
    return len([r for r in rows if tenant is None or r.tenant_id == tenant])


def test_enqueue_is_idempotent_and_requires_tenant():
    """同一道题只入队一次；没租户/没幂等键宁可不入队，也不能造孤儿行。"""
    async def scenario(db):
        one = await compliance_queue.enqueue(
            db, tenant_id=TENANT, dedup_key="question:1",
            content="题干 A", ref_type="question", ref_id=1, source="question",
        )
        await db.commit()
        assert one is not None and one.status == "pending"

        again = await compliance_queue.enqueue(
            db, tenant_id=TENANT, dedup_key="question:1", content="题干 A",
        )
        assert again is None  # 幂等
        assert await compliance_queue.enqueue(
            db, tenant_id=None, dedup_key="question:2", content="x"
        ) is None
        assert await compliance_queue.enqueue(
            db, tenant_id=TENANT, dedup_key=None, content="x"
        ) is None

        rows = (await db.execute(select(ComplianceItem))).scalars().all()
        assert len(rows) == 1

    asyncio.run(_run(scenario))


def test_list_compliance_is_tenant_scoped():
    """平台级传 None = 跨租户只读；普通角色只看到本租户（绝不能 IS NULL）。"""
    async def scenario(db):
        db.add_all([
            ComplianceItem(tenant_id=TENANT, type="题目", content="本校待审", status="pending"),
            ComplianceItem(tenant_id=OTHER, type="题目", content="别校待审", status="pending"),
        ])
        await db.commit()

        mine = await ops.list_compliance(db, tenant_id=TENANT)
        assert [r.content for r in mine] == ["本校待审"]
        all_rows = await ops.list_compliance(db, tenant_id=None)
        assert len(all_rows) == 2
        assert not await ops.list_compliance(db, tenant_id="no_such_tenant")

    asyncio.run(_run(scenario))


def test_review_compliance_records_actor_and_time():
    """审核结论必须留痕：谁、什么时候判的，否则结论不可追溯。"""
    async def scenario(db):
        obj = ComplianceItem(tenant_id=TENANT, type="题目", content="题干", status="pending")
        db.add(obj)
        await db.commit()

        await ops.review_compliance(db, obj, "approved", actor_id="t_1")
        assert obj.status == "approved"
        assert obj.reviewed_by == "t_1"
        assert obj.reviewed_at is not None

    asyncio.run(_run(scenario))


def test_teacher_entry_enqueues_pending_item():
    """教师录题 → 题目 draft + 一条待审项（同事务）。"""
    async def scenario(db):
        payload = QuestionCreate(
            subject="数学", grade="高三", type="单选",
            stem="已知 f(x)=x^2，求 f(2)", answer="B",
        )
        result = await question_entry.create_questions(
            db, [payload], {"user_id": "t_1", "tenant_id": TENANT, "role": "teacher"},
            transport=_exploding_transport, embed_config=FAKE_CFG,
        )
        assert result["created"] == 1

        rows = (await db.execute(select(ComplianceItem))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tenant_id == TENANT
        assert rows[0].status == "pending"
        assert rows[0].source == "question"
        assert rows[0].ref_type == "question"
        assert rows[0].ref_id == result["items"][0]["id"]
        assert rows[0].content == "已知 f(x)=x^2，求 f(2)"
        assert rows[0].dedup_key == f"question:{result['items'][0]['id']}"

    asyncio.run(_run(scenario))


def test_crawl_intake_enqueues_pending_item():
    """采集入库的 draft 题同样要进队列（不能因为是自动采集就绕过合规）。"""
    async def scenario(db):
        job = SimpleNamespace(
            id=1, path="P2", tenant_id=TENANT, name="测试采集任务",
        )
        out = await intake_svc.intake(
            db, job,
            items=[{"stem": "采集题：求 1+1", "answer": "2", "type": "填空",
                    "subject": "数学", "grade": "高三"}],
            run_id=None,
        )
        await db.commit()
        assert out["inserted"] == 1

        rows = (await db.execute(select(ComplianceItem))).scalars().all()
        assert len(rows) == 1
        assert rows[0].tenant_id == TENANT
        assert rows[0].source == "crawl"
        assert rows[0].ref_type == "question"

    asyncio.run(_run(scenario))
