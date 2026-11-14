"""2026-11-14 三处遗留收口的回归测试。

1. region_admin 对考试域管理端点不再 403，学生端点不放宽；
2. 交卷写侧 / 结果读侧 / 发布失效侧共用一套归一后的缓存键；
3. wrong_questions 补 tenant_id 后，后台列表按租户隔离、各写入口都带租户。
"""
import asyncio

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam, wrong_questions
from app.core.security import business_tenant_id, read_tenant_id, tenant_key
from app.models.course_kb import Base
from app.models.ops import ExamPaperQuestion, ExamSubmissionItem, WrongQuestion
from app.models.schemas import WrongQuestionOut
from app.services import learning, ops, wrongbook

FALLBACK = "school_pilot_01"


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


# ------------------------------------------------------- 1. 租户归一 / 角色闸门
def test_tenant_key_is_the_single_normalization_entry():
    for value in (None, "", "  ", "platform"):
        assert tenant_key(value) == FALLBACK
    assert tenant_key("demo") == "demo"
    assert tenant_key("henan_01") == "henan_01"
    # business_tenant_id 委托 tenant_key，缓存键与业务表过滤不会分叉
    assert business_tenant_id({"tenant_id": "platform"}) == FALLBACK
    assert business_tenant_id({"tenant_id": None}) == FALLBACK
    # 平台级只读返回 None（调用方必须不加 where），其余锁本租户
    assert read_tenant_id({"role": "admin", "tenant_id": "platform"}) is None
    assert read_tenant_id({"role": "region_admin", "tenant_id": "platform"}) is None
    assert read_tenant_id({"role": "teacher", "tenant_id": None}) == FALLBACK
    assert read_tenant_id({"role": "parent", "tenant_id": "demo"}) == "demo"


def test_exam_cache_keys_share_one_normalized_tenant():
    """三种来源（学生 JWT / 卷租户 / 空串）必须落到同一个 key。"""
    variants = (None, "", "  ", "platform", FALLBACK)
    assert len({exam._q_cache_key(v, 7) for v in variants}) == 1
    assert len({exam._r_cache_key(v, 7, 3) for v in variants}) == 1
    assert len({exam._r_cache_prefix(v, 7) for v in variants}) == 1
    assert exam._q_cache_key("platform", 7) == f"exam:v1:q:{FALLBACK}:7"
    assert exam._r_cache_key("platform", 7, 3) == f"exam:v1:r:{FALLBACK}:7:3"
    # 真实租户绝不折叠
    assert exam._r_cache_key("demo", 7, 3) == "exam:v1:r:demo:7:3"
    assert exam._q_cache_key("demo", 7) != exam._q_cache_key("platform", 7)
    # 交卷写侧与结果读侧的 paper_id 口径：sub.paper_id 恒等于 a.paper_id
    assert exam._r_cache_key(FALLBACK, 7, 3) == exam._r_cache_key(FALLBACK, 7 or 0, 3)


def test_exam_management_routes_allow_region_admin_but_student_routes_do_not():
    dep = exam.ExamManageDep
    for role in ("teacher", "admin", "school", "region_admin"):
        assert dep(user={"user_id": "x", "role": role})["role"] == role
    for role in ("student", "parent"):
        try:
            dep(user={"user_id": "x", "role": role})
            raise AssertionError(f"{role} 不应通过考试域管理闸门")
        except HTTPException as exc:
            assert exc.status_code == 403

    manage = [
        r for r in exam.router.routes
        if r.path.startswith("/exam/papers") or r.path.startswith("/exam/questions")
    ]
    assert len(manage) == 14, "考试域管理端点数量变了，白名单需同步复核"
    for route in manage:
        calls = [d.call for d in route.dependant.dependencies]
        assert dep in calls, f"{route.path} 漏挂 ExamManageDep"

    student_routes = list(exam.student_router.routes)
    assert len(student_routes) == 5
    for route in student_routes:
        calls = [d.call for d in route.dependant.dependencies]
        assert dep not in calls, f"{route.path} 学生端点被误放行"


# ------------------------------------------------------- 2. 后台错题列表租户隔离
def test_list_wrong_is_tenant_scoped_and_platform_reads_across():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all([
                WrongQuestion(student_id="s1", question="pilot", tenant_id=FALLBACK),
                WrongQuestion(student_id="s1", question="demo", tenant_id="demo"),
                # 迁移前的遗留 NULL 行：平台级能看到，本租户查询不应把它算进来
                WrongQuestion(student_id="s1", question="legacy"),
            ])
            await db.commit()

            pilot = await ops.list_wrong(db, "s1", tenant_id=FALLBACK)
            assert [r.question for r in pilot] == ["pilot"]
            demo = await ops.list_wrong(db, "s1", tenant_id="demo")
            assert [r.question for r in demo] == ["demo"]
            # None = 平台级跨租户只读，绝不能编译成 IS NULL
            across = await ops.list_wrong(db, "s1", tenant_id=None)
            assert len(across) == 3

            enriched = await ops.enrich_wrong(db, pilot)
            assert enriched[0]["tenant_id"] == FALLBACK
            # 响应模型声明了 tenant_id，否则 pydantic 会静默丢弃
            assert WrongQuestionOut.model_validate(
                {"student_id": "s1", "question": "q", "tenant_id": "demo"}
            ).tenant_id == "demo"
        await engine.dispose()

    asyncio.run(scenario())


def test_add_wrong_overrides_client_supplied_tenant(monkeypatch):
    captured = {}

    async def fake_create(db, payload):
        captured.update(payload)
        return payload

    monkeypatch.setattr(wrong_questions.ops, "create_wrong", fake_create)
    body = wrong_questions.WrongQuestion(
        student_id="s1", question="q", tenant_id="demo"
    )
    asyncio.run(wrong_questions.add_wrong(
        body, user={"role": "admin", "tenant_id": "platform"}, db=None
    ))
    assert captured["tenant_id"] == FALLBACK, "租户必须以登录态为准"


# ------------------------------------------------------- 3. 各写入口都带 tenant_id
def test_exam_and_app_wrongbook_writes_carry_tenant():
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            question = ExamPaperQuestion(
                id=11, paper_id=2, seq=1, content_hash="h", qtype="choice",
                stem="1 + 1 = ?", answer="B", knowledge_points=["加法"],
            )
            item = ExamSubmissionItem(
                id=21, submission_id=3, paper_question_id=11, seq=1,
                qtype="choice", student_answer="A", correct=False, status="auto",
            )
            db.add_all([question, item])
            await db.commit()

            # 交卷链路传 platform 占位，落库必须归一
            await wrongbook.sync_exam_wrong_questions(
                db, "s1", [(item, question)], tenant_id="platform"
            )
            await db.commit()
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert len(rows) == 1
            assert rows[0].tenant_id == FALLBACK

            # App 同步的新行按请求租户落库
            await wrongbook.upsert_sync(
                db, "s1",
                [{"client_id": "app-1", "question": "q2"}],
                tenant_id="demo",
            )
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            assert any(r.client_id == "app-1" and r.tenant_id == "demo" for r in rows)

            # 历史 NULL 行在同步命中时被补齐，且不覆盖已有真实租户
            db.add(WrongQuestion(student_id="s1", question="legacy", client_id="app-2"))
            await db.commit()
            await wrongbook.upsert_sync(
                db, "s1",
                [{"client_id": "app-2", "question": "legacy2"}],
                tenant_id=FALLBACK,
            )
            rows = (await db.execute(select(WrongQuestion))).scalars().all()
            by_cid = {r.client_id: r.tenant_id for r in rows}
            assert by_cid["app-2"] == FALLBACK
            assert by_cid["app-1"] == "demo"
        await engine.dispose()

    asyncio.run(scenario())


class _FakeResult:
    def __init__(self, obj=None):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj

    def all(self):
        return [self._obj] if self._obj is not None else []


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt):
        return _FakeResult(None)


def test_kafka_wrong_question_writes_tenant():
    db = _FakeSession()
    event = {
        "student_id": "s1", "tenant_id": "demo", "question": "q",
        "correct": False, "error_type": "calc",
    }
    assert asyncio.run(learning.record_wrong_question(db, event)) is True
    assert len(db.added) == 1
    assert db.added[0].tenant_id == "demo"

    # 事件缺租户时归一到兜底，绝不落 NULL
    db2 = _FakeSession()
    assert asyncio.run(learning.record_wrong_question(
        db2, {"student_id": "s2", "question": "q2", "correct": False}
    )) is True
    assert db2.added[0].tenant_id == FALLBACK
