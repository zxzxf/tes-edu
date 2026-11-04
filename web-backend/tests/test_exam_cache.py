"""学生端 Redis 读缓存 + 进卷合并接口（2026-11-04）。

锁四件事：
 1. 卷题快照首次查库回填、二次命中**不再碰数据库**（跨云每条 SQL ≈17ms）；
 2. 判分结果整包缓存，重复看结果不重跑 `_result_out` 的多条查询；
 3. 合并接口一次给全题干/是否已交/结果，且题干永不含答案、未提交不给结果；
 4. 租户隔离写进 key，Redis 异常静默降级直查 DB（缓存不能变成故障源）。

用内存 FakeRedis 替换 `app.core.cache._client`，不需要起 Redis 服务。
"""
import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam
from app.core import cache
from app.models.course_kb import Base
from app.models.ops import ExamAssignment, ExamPaper, ExamPaperQuestion, ExamSubmission

PILOT = "school_pilot_01"
STUDENT = "student@tes.edu"


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.gets = 0
        self.sets = 0

    async def get(self, key):
        self.gets += 1
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.sets += 1
        self.store[key] = value

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def scan_iter(self, match=None, count=100):
        prefix = (match or "").rstrip("*")
        for k in list(self.store):
            if k.startswith(prefix):
                yield k

    async def ping(self):
        return True


class BrokenRedis(FakeRedis):
    async def get(self, key):
        raise ConnectionError("redis down")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis down")


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "_client", fake)
    monkeypatch.setattr(cache, "_tried", True)
    return fake


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _user(tenant=PILOT, uid=STUDENT):
    return {"user_id": uid, "tenant_id": tenant, "role": "student"}


async def _seed(db):
    paper = ExamPaper(
        tenant_id=PILOT, title="缓存卷", paper_type="pure_choice",
        grade="高三", subject="数学", published=True,
    )
    db.add(paper)
    await db.flush()
    for seq in (1, 2):
        db.add(ExamPaperQuestion(
            paper_id=paper.id, seq=seq, qtype="choice",
            stem=f"题 {seq}", answer="A", score=5,
        ))
    await db.flush()
    a = ExamAssignment(
        paper_id=paper.id, tenant_id=PILOT, target_type="grade", target_grade="高三",
    )
    db.add(a)
    await db.flush()
    db.add(ExamSubmission(
        assignment_id=a.id, paper_id=paper.id, student_id=STUDENT,
        tenant_id=PILOT, status="graded",
    ))
    await db.commit()
    return paper, a


def _open(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory()


def test_question_cache_second_read_does_not_hit_db(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        queries = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        async with _open(engine) as db:
            paper, _ = await _seed(db)
            queries.clear()
            first = await exam._student_questions(db, paper.id, PILOT)
            first_n = len(queries)
            queries.clear()
            second = await exam._student_questions(db, paper.id, PILOT)
            second_n = len(queries)

        await engine.dispose()
        return first, first_n, second, second_n

    first, n1, second, n2 = asyncio.run(scenario())
    assert [q.seq for q in first] == [1, 2]
    assert [q.stem for q in second] == [q.stem for q in first]
    assert n1 >= 1, "首次必须查库"
    assert n2 == 0, f"第二次应命中 Redis，实际又发了 {n2} 条 SQL"
    assert redis.sets >= 1


def test_bundle_returns_everything_in_one_call(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _open(engine) as db:
            paper, a = await _seed(db)
            bundle = await exam.get_exam_bundle(assignment_id=a.id, user=_user(), db=db)
        await engine.dispose()
        return bundle

    b = asyncio.run(scenario())
    assert len(b.questions) == 2
    assert b.submitted is True, "种子里已有提交记录"
    assert b.result is not None
    assert b.title == "缓存卷"
    # 题干下发绝不能带答案：StudentQuestionView 本身没有 answer 字段
    assert all(not hasattr(q, "answer") for q in b.questions)
    assert "exam:v1:q:" in "".join(redis.store.keys())
    assert "exam:v1:r:" in "".join(redis.store.keys())


def test_bundle_without_submission_has_no_result(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _open(engine) as db:
            _, a = await _seed(db)
            b = await exam.get_exam_bundle(
                assignment_id=a.id, user=_user(uid="fresh@tes.edu"), db=db
            )
        await engine.dispose()
        return b

    b = asyncio.run(scenario())
    assert b.submitted is False
    assert b.result is None


def test_result_cache_avoids_rebuilding_result(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        queries = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        async with _open(engine) as db:
            paper, a = await _seed(db)
            sub = (await db.execute(select(ExamSubmission))).scalars().first()
            queries.clear()
            await exam._result_cached(db, sub, PILOT, paper.id)
            first_n = len(queries)
            queries.clear()
            again = await exam._result_cached(db, sub, PILOT, paper.id)
            second_n = len(queries)
        await engine.dispose()
        return first_n, second_n, again.title

    n1, n2, title = asyncio.run(scenario())
    assert title == "缓存卷"
    assert n1 >= 1
    assert n2 == 0, f"结果缓存命中后不该再查库，实际 {n2} 条 SQL"


def test_invalidate_paper_clears_questions_and_results(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _open(engine) as db:
            paper, _ = await _seed(db)
            await exam._student_questions(db, paper.id, PILOT)
        await engine.dispose()
        return paper.id

    pid = asyncio.run(scenario())
    assert any(k.startswith(f"exam:v1:q:{PILOT}:") for k in redis.store)
    redis.store[f"exam:v1:r:{PILOT}:{pid}:999"] = "{}"
    asyncio.run(exam.invalidate_paper(PILOT, pid))
    assert not any(k.startswith(f"exam:v1:q:{PILOT}:{pid}") for k in redis.store)
    assert not any(k.startswith(f"exam:v1:r:{PILOT}:{pid}:") for k in redis.store)


def test_tenant_isolation_in_cache_key(redis):
    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _open(engine) as db:
            paper, _ = await _seed(db)
            await exam._student_questions(db, paper.id, PILOT)
            await exam._student_questions(db, paper.id, "another_school")
        await engine.dispose()

    asyncio.run(scenario())
    keys = list(redis.store)
    assert any(k.startswith(f"exam:v1:q:{PILOT}:") for k in keys)
    assert any(k.startswith("exam:v1:q:another_school:") for k in keys), (
        "不同租户必须用不同 key，否则会跨校串卷"
    )


def test_redis_failure_falls_back_to_db(monkeypatch):
    monkeypatch.setattr(cache, "_client", BrokenRedis())
    monkeypatch.setattr(cache, "_tried", True)

    async def scenario():
        engine = _engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with _open(engine) as db:
            paper, a = await _seed(db)
            qs = await exam._student_questions(db, paper.id, PILOT)
            b = await exam.get_exam_bundle(assignment_id=a.id, user=_user(), db=db)
        await engine.dispose()
        return len(qs), len(b.questions)

    assert asyncio.run(scenario()) == (2, 2), "Redis 挂了也必须照常返回数据"
