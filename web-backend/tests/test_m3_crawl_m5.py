"""M3-6 错题→知识点→背诵卡；M3-8/M5 采集 cron/预算/合规/解析/知识点审核。"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.course_kb import Base, CourseQuestion
from app.models.ops import CrawlJob, CrawlRun, DrillCard, KpNode, WrongQuestion
from app.services import course_pipeline, drill_link
from app.crawl_service import scheduler as crawl


def _engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_function("btrim", 1, lambda s: s.strip() if isinstance(s, str) else s)

    return engine


async def _bootstrap(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for col in ("code", "chapter", "section", "importance", "understanding",
                    "memory", "analogy"):
            try:
                await conn.execute(text(f"ALTER TABLE kb_knowledge_points ADD COLUMN {col} TEXT"))
            except Exception:
                pass
        await conn.execute(text(
            "INSERT INTO kb_knowledge_points (id, subject, title, content, code, memory) "
            "VALUES (1, '数学', '等比数列', '...', 'MA-K1', '首项公比先定，通项公式记牢')"
        ))


# ---------------------------------------------------------------- M3-6
def test_wrong_exam_item_creates_one_card_per_kp():
    """判错 2 道同一 kp 的题 -> 只建 1 张卡（否则复习队列被灌满）。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            rows = [
                WrongQuestion(student_id="stu-1", question=f"题{i}", knowledge_point="等比数列",
                              source="exam", client_id=f"exam:{i}")
                for i in (1, 2)
            ]
            db.add_all(rows)
            await db.commit()
            out = await drill_link.on_wrong_exam_items(
                db, student_id="stu-1", tenant_id="school-1", wrong_rows=rows)
            await db.commit()
            cards = (await db.execute(select(DrillCard))).scalars().all()
            assert len(cards) == 1
            assert cards[0].kp == "MA-K1"
            assert cards[0].back == "首项公比先定，通项公式记牢"
            assert out["unresolved"] == []
        await engine.dispose()

    asyncio.run(scenario())


def test_mastery_up_pushes_due_but_keeps_ease():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            card = DrillCard(student_id="stu-1", tenant_id="school-1", kp="MA-K1",
                             front="f", back="b", source="wrong", ease=2.5,
                             interval_days=0, due_at=datetime.now(timezone.utc))
            db.add(card)
            await db.commit()
            now = datetime.now(timezone.utc)
            out = await drill_link.on_mastery_up(
                db, student_id="stu-1", kp_code="MA-K1", now=now)
            assert out is not None
            assert out.due_at > now
            assert out.ease == 2.5  # 一次做对不改 SM-2 参数
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M3-8 采集
def _job(**kw):
    base = dict(id=1, tenant_id="school-1", name="job", path="P2",
                budget_limit=3, spent=0, status="paused")
    base.update(kw)
    return CrawlJob(**base)


def test_cron_parse_and_match():
    assert crawl.parse_cron("*/5 * * * *") is not None
    assert crawl.parse_cron("bad cron") is None
    assert crawl.parse_cron("61 * * * *") is None
    when = datetime(2026, 11, 3, 10, 15, tzinfo=timezone.utc)
    assert crawl.matches_cron("15 10 * * *", when) is True
    assert crawl.matches_cron("*/5 * * * *", when) is True
    assert crawl.matches_cron("16 10 * * *", when) is False
    assert crawl.matches_cron("bad", when) is False


def test_cron_next_run_at_returns_future():
    when = datetime(2026, 11, 3, 10, 15, tzinfo=timezone.utc)
    nxt = crawl.next_run_at("*/5 * * * *", when)
    assert nxt == datetime(2026, 11, 3, 10, 20, tzinfo=timezone.utc)


def test_budget_limit_stops_insert_loop():
    """budget_limit=1 -> 只入 1 条就停，任务转 stopped。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job(budget_limit=1, spent=0, status="active")
            db.add(job)
            await db.commit()
            items = [
                dict(subject="数学", grade="高三", type="单选", stem=f"题{i}", answer="A",
                     content_hash=f"c{i}", tenant_id="school-1", source_tag="crawl",
                     status="draft")
                for i in range(3)
            ]
            run = await crawl.run_job(db, job, items=items)
            await db.commit()
            assert run.inserted == 1
            assert run.budget_stop is True
            assert run.status == "stopped"
            assert job.spent == 1
            assert job.status == "stopped"
        await engine.dispose()

    asyncio.run(scenario())


def test_crawl_dedup_by_content_hash():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(CourseQuestion(subject="数学", grade="高三", type="单选", stem="已有",
                                  answer="A", content_hash="dup", tenant_id="school-1"))
            job = _job(budget_limit=10)
            db.add(job)
            await db.commit()
            run = await crawl.run_job(db, job, items=[
                dict(subject="数学", grade="高三", type="单选", stem="重复", answer="A",
                     content_hash="dup", tenant_id="school-1", status="draft"),
                dict(subject="数学", grade="高三", type="单选", stem="新的", answer="B",
                     content_hash="new", tenant_id="school-1", status="draft"),
            ])
            await db.commit()
            assert run.inserted == 1
            assert run.duplicated == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_compliance_blocklist_rejects_source():
    assert crawl.compliance_reject("http://copyright-reserved.example/x") is not None
    assert crawl.compliance_reject("https://example.com/textbook") is None


def test_retry_rejected_when_budget_exhausted():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job(budget_limit=1, spent=1, status="stopped")
            db.add(job)
            await db.commit()
            run = await crawl.retry_run(db, "school-1", job.id)
            assert run is not None and run.budget_stop is True
            assert "预算已用尽" in (run.error or "")
        await engine.dispose()

    asyncio.run(scenario())


def test_run_metrics_success_and_dedup_rate():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all([
                CrawlRun(job_id=1, tenant_id="school-1", status="ok",
                         inserted=3, duplicated=1),
                CrawlRun(job_id=1, tenant_id="school-1", status="failed", error="boom"),
            ])
            await db.commit()
            out = await crawl.run_metrics(db, "school-1")
            assert out["runs"] == 2
            assert out["success_rate"] == 0.5
            assert out["dedup_rate"] == 0.25
        await engine.dispose()

    asyncio.run(scenario())


def test_set_job_status_activate_recomputes_next_run_at():
    """重新启用要重算 next_run_at，否则过期时间会让调度器每 tick 重复跑。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job(status="paused", schedule="0 3 * * *", next_run_at=None)
            db.add(job)
            await db.commit()
            out = await crawl.set_job_status(db, "school-1", job.id, "active")
            await db.commit()
            assert out is not None and out.status == "active"
            assert out.next_run_at is not None
            assert out.next_run_at > datetime.now(timezone.utc) - timedelta(minutes=1)
        await engine.dispose()

    asyncio.run(scenario())


def test_set_job_status_rejects_invalid_cron_on_activate():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = _job(status="paused", schedule="not a cron")
            db.add(job)
            await db.commit()
            job_id = job.id
            with pytest.raises(ValueError):
                await crawl.set_job_status(db, "school-1", job_id, "active")
            # 校验先于赋值，失败后不需要回滚也不会留下半改状态
            db.expire_all()
            row = (await db.execute(select(CrawlJob).where(CrawlJob.id == job_id))).scalar_one()
            assert row.status == "paused"
            with pytest.raises(ValueError):
                await crawl.set_job_status(db, "school-1", job_id, "bogus")
        await engine.dispose()

    asyncio.run(scenario())


def test_due_jobs_only_active_and_handles_naive_datetime():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            now = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
            db.add_all([
                # SQLite 读回来是 naive，必须按 UTC 比较
                _job(id=1, status="active", schedule="0 3 * * *",
                     next_run_at=datetime(2026, 11, 3, 9, 0)),
                _job(id=2, status="active", schedule="0 3 * * *",
                     next_run_at=now),
                _job(id=3, status="paused", schedule="0 3 * * *",
                     next_run_at=datetime(2026, 11, 3, 9, 0)),
                _job(id=4, status="active", schedule="0 3 * * *", next_run_at=None),
                _job(id=5, status="active", schedule="  ", next_run_at=None),
            ])
            await db.commit()
            out = await crawl.due_jobs(db, now)
            ids = sorted(j.id for j in out)
            assert ids == [1, 2, 4]
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M5-2
def test_parse_outline_builds_chapter_tree():
    raw = """第一章 数列
第一节 等差数列
等差数列的通项公式
前n项和公式
第二节 等比数列
等比数列的定义

第二章 三角函数
正弦函数图像
"""
    out = course_pipeline.parse_outline(raw, source_type="textbook")
    assert out["chapter_count"] == 2
    assert out["chapters"][0]["sections"][0]["title"].startswith("第一节")
    assert out["point_count"] >= 3
    assert out["parser"] == "textbook"


def test_parse_outline_handles_markdown():
    raw = """# 第一章
## 1.1 集合
集合的含义
# 第二章
## 2.1 函数
函数的定义域
"""
    out = course_pipeline.parse_outline(raw)
    assert out["chapter_count"] == 2
    assert out["point_count"] == 2


# ---------------------------------------------------------------- M5-2 B 端联调契约
_PILOT_OUTLINE = """# 第一章 数列
## 第一节 等差数列
等差数列的通项公式
等差数列的前n项和
## 第二节 等比数列
等比数列的通项公式
# 第二章 立体几何
## 第一节 空间向量
空间向量的坐标运算
"""


def test_parse_outline_emits_stable_short_codes_for_all_layers():
    """2026-11-11 B 端联调：/course/parse 必须给章/节/点都发 ≤32 的稳定 code。

    旧版只给 ``code_hint``，CourseIntake.vue 却按 ``n.code`` 组装
    POST /course/kp-nodes 载荷，整批候选 code=undefined 被 422 拦下。
    这里的具体 code 值与前端 utils/courseOutline.test.js 的 fixture 同源，
    作为跨语言契约锚点，任一侧改算法都会立刻暴露。
    """
    out = course_pipeline.parse_outline(_PILOT_OUTLINE)
    assert out["chapter_count"] == 2
    assert out["point_count"] == 4
    assert out["chapters"][0]["code"] == "第一章_数列_4ef3ec"
    assert out["chapters"][0]["sections"][0]["points"][0]["code"] == "等差数列的通项公式_65001a"

    codes = []
    for ch in out["chapters"]:
        codes.append(ch["code"])
        for sec in ch["sections"]:
            codes.append(sec["code"])
            for point in sec["points"]:
                codes.append(point["code"])
    assert len(codes) == 9  # 2 章 + 3 节 + 4 点，三层一个不丢
    assert all(c and len(c) <= 32 for c in codes)  # kp_node.code = String(32)
    assert len(set(codes)) == len(codes)          # 全局唯一
    assert all(not any(x in c for x in "/#?%") for c in codes)  # REST 路径安全
    again = course_pipeline.parse_outline(_PILOT_OUTLINE)
    assert [c["code"] for c in again["chapters"]] == [c["code"] for c in out["chapters"]]


def test_parse_outline_same_section_title_under_two_chapters_is_distinct():
    raw = """# 第一章 力学
## 小结
牛顿第一定律
# 第二章 热学
## 小结
比热容
"""
    out = course_pipeline.parse_outline(raw)
    a = out["chapters"][0]["sections"][0]["code"]
    b = out["chapters"][1]["sections"][0]["code"]
    assert a != b  # 父 code 参与 hash，避免同名小节撞车


def test_kp_upsert_schema_rejects_overlong_code():
    """入口挡住超长 code，避免 DB DataError 变成 500。"""
    from pydantic import ValidationError

    from app.crawl_service.api import KpUpsert

    assert KpUpsert(code="C1", name="数列").code == "C1"
    with pytest.raises(ValidationError):
        KpUpsert(code="x" * 33, name="数列")
    with pytest.raises(ValidationError):
        KpUpsert(code="C1", name="数列", parent_code="p" * 33)


# ---------------------------------------------------------------- M5-3/4
def test_kp_node_defaults_to_draft_and_review_publishes():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            out = await course_pipeline.upsert_kp_nodes(
                db, tenant_id="school-1",
                items=[{"code": "MA-NEW", "name": "新知识点", "subject": "数学"}],
            )
            await db.commit()
            assert out["created"] == 1
            row = (await db.execute(select(KpNode).where(KpNode.code == "MA-NEW"))).scalar()
            assert row.status == "draft"

            # 重复 upsert 不覆盖（可能是教师改过的）
            again = await course_pipeline.upsert_kp_nodes(
                db, tenant_id="school-1",
                items=[{"code": "MA-NEW", "name": "改名"}],
            )
            assert again["created"] == 0 and again["skipped"] == 1

            res = await course_pipeline.review_kp_node(
                db, tenant_id="school-1", code="MA-NEW", status="published",
                reviewer="t1",
            )
            await db.commit()
            assert res["status"] == "published"
            published = (await db.execute(text(
                "SELECT code FROM kb_knowledge_points WHERE code='MA-NEW'"))).first()
            assert published is not None
        await engine.dispose()

    asyncio.run(scenario())