"""题库租户隔离（M0-4 第 3 步）。

背景：`course_kb` 在 `0005_tenant_source_tag.sql` 里已有 `tenant_id`，写入侧
（M0-1 录题）也只取登录态。但读取侧 **没有任何租户过滤**：`GET /api/admin/questions`
与 `/search` 会把全表返回，任何学校教师都能看到别校题目（含别校草稿）。

本文件锁定两个层面：
1. service 层收 `tenant_id` 后只返回本租户行；传 None 保持旧行为（运维脚本用）；
2. 路由层从登录态取租户并透传，不同租户 token 查同一关键字互不串题。
"""
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import questions as questions_api
from app.models.course_kb import Base, CourseQuestion
from app.services import course_kb as svc

PILOT = "school_pilot_01"
OTHER = "demo"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _seed(db):
    rows = [
        CourseQuestion(subject="数学", stem="试点校的题", type="单选",
                       tenant_id=PILOT, status="published", content_hash="h1"),
        CourseQuestion(subject="数学", stem="试点校草稿", type="单选",
                       tenant_id=PILOT, status="draft", content_hash="h2"),
        CourseQuestion(subject="数学", stem="别校的题", type="单选",
                       tenant_id=OTHER, status="published", content_hash="h3"),
        CourseQuestion(subject="数学", stem="别校草稿", type="单选",
                       tenant_id=OTHER, status="draft", content_hash="h4"),
    ]
    db.add_all(rows)
    await db.commit()


def _run(scenario):
    async def _main():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with factory() as db:
                await _seed(db)
                await scenario(db)
        finally:
            await engine.dispose()

    asyncio.run(_main())


def test_list_questions_filters_by_tenant():
    async def scenario(db):
        rows = await svc.list_questions(db, limit=50, tenant_id=PILOT)
        stems = {r.stem for r in rows}
        assert stems == {"试点校的题", "试点校草稿"}, stems

    _run(scenario)


def test_list_questions_without_tenant_is_unchanged():
    """不传 tenant_id 时保持旧行为，供一次性运维/脚本调用。"""
    async def scenario(db):
        rows = await svc.list_questions(db, limit=50)
        assert len(rows) == 4

    _run(scenario)


def test_search_questions_filters_by_tenant():
    async def scenario(db):
        rows = await svc.search_questions(db, q="草稿", tenant_id=PILOT, limit=50)
        assert {r.stem for r in rows} == {"试点校草稿"}

        rows = await svc.search_questions(db, q="草稿", tenant_id=OTHER, limit=50)
        assert {r.stem for r in rows} == {"别校草稿"}

    _run(scenario)


def test_search_questions_other_tenant_returns_empty():
    """关键字命中别校题时，本租户必须返回空——不能靠"查不到"来兜底。"""
    async def scenario(db):
        rows = await svc.search_questions(db, q="别校", tenant_id=PILOT, limit=50)
        assert rows == []

    _run(scenario)


def test_list_route_passes_tenant_from_login_state():
    """路由层必须从登录态取租户；demo 教师看不到试点校的题。"""
    async def scenario(db):
        rows = await questions_api.list_questions(
            limit=50,
            user={"user_id": "t_demo", "tenant_id": OTHER, "role": "teacher"},
            db=db,
        )
        assert {r.stem for r in rows} == {"别校的题", "别校草稿"}

    _run(scenario)


def test_search_route_is_tenant_scoped():
    async def scenario(db):
        rows = await questions_api.search(
            q="题",
            subject=None,
            type=None,
            knowledge_point=None,
            limit=50,
            user={"user_id": "t_pilot", "tenant_id": PILOT, "role": "teacher"},
            db=db,
        )
        assert {r.stem for r in rows} == {"试点校的题"}

    _run(scenario)


def test_route_falls_back_to_pilot_when_tenant_missing():
    """网关注入头缺失时按试点校兜底，与 exam/student_profile 同口径。"""
    async def scenario(db):
        rows = await questions_api.list_questions(
            limit=50, user={"user_id": "t_legacy", "role": "teacher"}, db=db
        )
        assert {r.stem for r in rows} == {"试点校的题", "试点校草稿"}

    _run(scenario)