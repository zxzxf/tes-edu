"""M0-1 教师录题：入库 + 向量双写 + 降级 + 幂等。

覆盖卡面四条可测验收：
- 录 1 题后 course_kb 与 course_kb_embeddings 均有该 content_hash 行；
- 向量生成失败时题目仍入库，且**保持 draft**（不得因模型端点挂了就丢题或 500）；
- content_hash 幂等（重复录不产生第二行，与 Excel 导入同一指纹口径）；
- 录题**不能**直接落 published（审核隔离红线）。
"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.course_kb import Base, CourseKbEmbedding, CourseQuestion, QuestionCreate
from app.services import question_entry
from app.services.parser import normalize_question

TENANT = "school_pilot_01"
TEACHER = {"user_id": "t_1", "tenant_id": TENANT, "role": "teacher"}
#: 测试注入的 embedding 配置（不触网）：SSRF 闸门只做字符串校验，example.com 可过
FAKE_CFG = {
    "source": "tenant",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-test",
    "model": "qwen3.7-text-embedding",
}
DIM = 1024


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _ok_transport(dim: int = DIM):
    """假 embedding 端点：按输入条数返回同序向量。"""

    async def _t(base_url, api_key, model, texts, timeout):
        return _FakeResp(
            {"data": [{"index": i, "embedding": [0.01] * dim} for i in range(len(texts))]}
        )

    return _t


def _bad_dim_transport():
    async def _t(base_url, api_key, model, texts, timeout):
        return _FakeResp({"data": [{"index": i, "embedding": [0.01] * 8} for i in range(len(texts))]})

    return _t


def _exploding_transport():
    async def _t(base_url, api_key, model, texts, timeout):
        raise RuntimeError("connection reset by peer")

    return _t


def _rate_limited_transport():
    async def _t(base_url, api_key, model, texts, timeout):
        return _FakeResp({"error": "quota"}, status_code=429)

    return _t


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _run(scenario):
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db:
            await scenario(db)
    finally:
        await engine.dispose()


def _payload(**over) -> QuestionCreate:
    base = dict(
        subject="数学",
        grade="高三",
        type="单选",
        stem="已知 f(x)=x^2，求 f(2)",
        options=["A. 2", "B. 4", "C. 8", "D. 16"],
        answer="B",
        analysis="代入得 4",
        difficulty=2,
        knowledge_points=["函数的表示"],
    )
    base.update(over)
    return QuestionCreate(**base)


def test_create_question_writes_draft_and_dual_writes_embedding():
    async def scenario(db):
        res = await question_entry.create_questions(
            db, [_payload()], TEACHER, transport=_ok_transport(), embed_config=FAKE_CFG
        )

        assert res["created"] == 1 and res["duplicated"] == 0
        assert res["embedding"]["ok"] is True
        assert res["embedding"]["written"] == 1
        assert res["embedding"]["model"] == FAKE_CFG["model"]

        kb = (await db.execute(select(CourseQuestion))).scalars().all()
        assert len(kb) == 1
        row = kb[0]
        assert row.status == "draft"            # 审核隔离：录题不能直接发布
        assert row.tenant_id == TENANT
        assert row.source_tag == "teacher"
        assert row.type == "单选"
        assert row.answer_values == ["B"]       # 标答预展开（M2-4 口径）

        emb = (await db.execute(select(CourseKbEmbedding))).scalars().all()
        assert len(emb) == 1
        assert emb[0].content_hash == row.content_hash
        assert emb[0].type == "单选"
        assert emb[0].difficulty == "2"         # 本表 difficulty 是 TEXT
        assert "代入得 4" in emb[0].content      # 送检索文本含解析

    asyncio.run(_run(scenario))


def test_embedding_failure_still_stores_question_as_draft():
    """模型端点挂掉（异常/429/维度不符）绝不能让题目丢失，也绝不能 500。"""

    async def scenario(db):
        for transport in (_exploding_transport(), _rate_limited_transport(), _bad_dim_transport()):
            db.expunge_all()
            payload = _payload(stem=f"题目-{id(transport)}")
            res = await question_entry.create_questions(
                db, [payload], TEACHER, transport=transport, embed_config=FAKE_CFG
            )
            assert res["created"] == 1
            assert res["embedding"]["ok"] is False
            assert res["embedding"]["written"] == 0
            assert res["embedding"]["reason"]

            row = (
                await db.execute(
                    select(CourseQuestion).where(
                        CourseQuestion.content_hash == res["items"][0]["content_hash"]
                    )
                )
            ).scalars().first()
            assert row is not None and row.status == "draft"

        # 三个失败场景都不该留下向量行
        emb = (await db.execute(select(CourseKbEmbedding))).scalars().all()
        assert emb == []

    asyncio.run(_run(scenario))


def test_unconfigured_embedding_degrades_without_network():
    """未配置端点时直接降级（reason 指明未配置），不发起任何请求。"""

    async def scenario(db):
        res = await question_entry.create_questions(
            db,
            [_payload()],
            TEACHER,
            transport=_ok_transport(),
            embed_config={"source": "none", "base_url": "", "api_key": "", "model": ""},
        )
        assert res["created"] == 1
        assert res["embedding"]["ok"] is False
        assert "未配置" in res["embedding"]["reason"]

    asyncio.run(_run(scenario))


def test_duplicate_content_hash_is_idempotent():
    """与 Excel 导入共用同一指纹算法：重复录不会产生第二行。"""

    async def scenario(db):
        first = await question_entry.create_questions(
            db, [_payload()], TEACHER, transport=_ok_transport(), embed_config=FAKE_CFG
        )
        second = await question_entry.create_questions(
            db, [_payload()], TEACHER, transport=_ok_transport(), embed_config=FAKE_CFG
        )

        assert first["created"] == 1
        assert second["created"] == 0 and second["duplicated"] == 1
        assert second["items"][0]["id"] == first["items"][0]["id"]
        assert len((await db.execute(select(CourseQuestion))).scalars().all()) == 1
        assert len((await db.execute(select(CourseKbEmbedding))).scalars().all()) == 1

    asyncio.run(_run(scenario))


def test_entry_cannot_force_published_status():
    """请求体就算带上 status=published 也必须落 draft（审核隔离红线）。"""

    async def scenario(db):
        # QuestionCreate 的 extra=ignore：客户端夹带 status 会被丢弃，不会流到落库
        payload = QuestionCreate(
            subject="数学", grade="高三", type="单选", stem="带 status 的题",
            options=["A. 1", "B. 2"], answer="A", status="published",
        )
        res = await question_entry.create_questions(
            db, [payload], TEACHER, transport=_ok_transport(), embed_config=FAKE_CFG
        )
        row = (await db.execute(select(CourseQuestion))).scalars().first()
        assert row.status == "draft"
        assert res["items"][0]["status"] == "draft"

    asyncio.run(_run(scenario))


def test_choice_type_refines_single_and_multi():
    async def scenario(db):
        await question_entry.create_questions(
            db,
            [
                _payload(qtype="choice", choice_type="multi", stem="多选题干"),
                _payload(qtype="choice", choice_type="single", stem="单选题干"),
                _payload(qtype="blank", choice_type="multi", stem="填空题干", options=None, answer="2"),
            ],
            TEACHER,
            transport=_ok_transport(),
            embed_config=FAKE_CFG,
        )
        rows = {
            r.stem: r
            for r in (await db.execute(select(CourseQuestion))).scalars().all()
        }
        assert rows["多选题干"].type == "多选"
        assert rows["单选题干"].type == "单选"
        # 非选择题不保留 choice_type，避免"填空题带 single 标记"这种自相矛盾的数据
        assert rows["填空题干"].type == "填空"
        assert rows["填空题干"].choice_type is None

    asyncio.run(_run(scenario))


def test_missing_tenant_is_rejected():
    async def scenario(db):
        with pytest.raises(HTTPException) as exc:
            await question_entry.create_questions(db, [_payload()], {"user_id": "x"})
        assert exc.value.status_code == 400

    asyncio.run(_run(scenario))


def test_blank_stem_rejected():
    async def scenario(db):
        with pytest.raises(HTTPException) as exc:
            await question_entry.create_questions(db, [_payload(stem="   ")], TEACHER)
        assert exc.value.status_code == 422

    asyncio.run(_run(scenario))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("函数；导数", ["函数", "导数"]),
        ("函数、导数/不等式|数列,概率，统计", ["函数", "导数", "不等式", "数列", "概率", "统计"]),
        ("  a ; b ;c  ", ["a", "b", "c"]),
    ],
)
def test_knowledge_point_separators(raw, expected):
    """教研表里全角顿号/分号、斜杠、竖线、中英文逗号混用，都得拆开。"""
    out = normalize_question({"subject": "数学", "stem": "题干", "知识点": raw})
    assert out["knowledge_points"] == expected


def test_english_qtype_maps_to_chinese_bank_types():
    """录题页用 choice/blank/subjective 枚举，落库必须是库里既有中文口径。"""
    for raw, expected in (("blank", "填空"), ("subjective", "解答"), ("choice", "选择")):
        out = normalize_question({"subject": "数学", "stem": "题干", "qtype": raw})
        assert out["type"] == expected, raw
