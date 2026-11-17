"""T-CAP4 入库三闸：教师录题 / 批量导入两条人工来源链路的 hook。

覆盖要点（三条链路统一走三闸，但写回口径分链路）：

- 纯函数 ``verdict_to_fields``：quarantine 一律落 tag；manual 仅在 ``tag_manual`` 时落；
- 教师录题：选项越界 / 缺答案 / 本校同题异 hash → ``quarantine`` + ``teacher:guard_quarantine``；
- 教师录题：**别校**有同题（指纹相同）不算本校重复 → 不隔离（去重按租户收窄）；
- 教师录题：合法选择题 manual 不打标（``source_tag`` 保持 ``teacher``）、status 保持 draft；
- 批量导入：同样三分流，隔离 tag 为 ``import:guard_quarantine``；
- 两条链路各自可关停（``TEACHER_GUARD_ENABLED`` / ``IMPORT_GUARD_ENABLED``）回到 legacy。
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.intake_guard import (
    VERDICT_MANUAL, VERDICT_PASS, VERDICT_QUARANTINE, verdict_to_fields,
)
from app.models.course_kb import Base, CourseQuestion, QuestionCreate
from app.services import course_kb as ck
from app.services import kb_guard, question_entry
from app.services.parser import parse_records

TENANT = "school_pilot_01"
OTHER = "school_other_02"
TEACHER = {"user_id": "t_1", "tenant_id": TENANT, "role": "teacher"}
NO_EMBED = {"source": "none", "base_url": "", "api_key": "", "model": ""}


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


def _payload(**over) -> QuestionCreate:
    base = dict(
        subject="数学", grade="高三", type="单选",
        stem="设集合 A={x|-2<x<4}，B={2,3,4,5}，则 A∩B=( )",
        options=["A. {2}", "B. {3}", "C. {2,3}", "D. {2,3,4}"],
        answer="C", analysis="取交集", difficulty=2,
        knowledge_points=["集合的运算"],
    )
    base.update(over)
    return QuestionCreate(**base)


async def _create(db, payloads, user=TEACHER):
    return await question_entry.create_questions(
        db, payloads, user, embed_config=NO_EMBED,
    )


# ---------------------------------------------------------------- 纯函数
def test_verdict_to_fields_policy():
    """隔离一律落 tag；manual 只在 tag_manual=True 时落；pass 不动。"""
    q = verdict_to_fields(VERDICT_QUARANTINE, base_tag="teacher")
    assert q == {"status": "quarantine", "source_tag": "teacher:guard_quarantine"}

    assert verdict_to_fields(VERDICT_MANUAL, base_tag="teacher", tag_manual=False) == {}
    assert verdict_to_fields(VERDICT_MANUAL, base_tag="teacher", tag_manual=True) == {
        "source_tag": "teacher:guard_manual_review"
    }
    # 采集链路口径与既有线上 tag 逐字一致
    assert verdict_to_fields(VERDICT_MANUAL, base_tag="auto_crawl", tag_manual=True) == {
        "source_tag": "auto_crawl:guard_manual_review"
    }
    assert verdict_to_fields(VERDICT_PASS, base_tag="import", tag_manual=True) == {}


# ---------------------------------------------------------------- 教师录题
def test_teacher_answer_out_of_range_quarantined():
    """选项只有 A-D 却标答 H：明确错误，直接隔离，且回写给调用方。"""
    async def scenario(db):
        res = await _create(db, [_payload(answer="H")])
        assert res["created"] == 1
        assert res["guard"]["chain"] == "teacher"
        assert res["guard"]["quarantine"] == 1
        assert "answer_out_of_range" in res["guard"]["by_gate"]

        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "quarantine"
        assert row.source_tag == "teacher:guard_quarantine"
        # items 里的 status 要跟着更新，否则前端会显示成「待审」
        assert res["items"][0]["status"] == "quarantine"

    asyncio.run(_run(scenario))


def test_teacher_missing_answer_quarantined():
    """无标答的题进不了判分/组卷：入库即隔离，不留在正常 draft 流。"""
    async def scenario(db):
        res = await _create(db, [_payload(answer=None)])
        assert res["guard"]["quarantine"] == 1
        assert "missing_answer" in res["guard"]["by_gate"]
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "quarantine"

    asyncio.run(_run(scenario))


def test_teacher_text_duplicate_in_same_tenant_quarantined():
    """本校已有同题（仅标点/全角差异，content_hash 不同）→ 隔离。"""
    async def scenario(db):
        first = await _create(db, [_payload()])
        assert first["created"] == 1

        # 半角逗号 + 全角括号差异：hash 变、指纹不变
        dup = _payload(stem="设集合 A={x|-2<x<4}, B={2,3,4,5}, 则 A∩B=( )")
        res = await _create(db, [dup])
        assert res["created"] == 1, "hash 不同，应作为新行插入"
        assert res["guard"]["quarantine"] == 1
        assert "dedup_text" in res["guard"]["by_gate"]

        rows = (await db.execute(select(CourseQuestion))).scalars().all()
        assert len(rows) == 2
        assert {r.status for r in rows} == {"draft", "quarantine"}

    asyncio.run(_run(scenario))


def test_teacher_same_question_in_other_tenant_not_quarantined():
    """别校有同题不算本校重复：去重索引按租户收窄，避免误杀各校自建题库。"""
    async def scenario(db):
        await _create(db, [_payload(stem="某校独有题：求 f(2) 的值")])
        other = {"user_id": "t_2", "tenant_id": OTHER, "role": "teacher"}
        # 换标点制造不同 hash，但题干指纹相同
        res = await _create(
            db, [_payload(stem="某校独有题: 求 f(2) 的值")], user=other,
        )
        assert res["created"] == 1
        assert res["guard"]["quarantine"] == 0
        row = (await db.execute(
            select(CourseQuestion).where(CourseQuestion.tenant_id == OTHER)
        )).scalars().one()
        assert row.status == "draft"

    asyncio.run(_run(scenario))


def test_teacher_valid_choice_keeps_source_tag():
    """合法选择题 = manual_review，但人工链路口径不打标：来源语义必须保留。"""
    async def scenario(db):
        res = await _create(db, [_payload()])
        assert res["guard"]["pass"] == 0
        assert res["guard"]["manual_review"] == 1
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "teacher"

    asyncio.run(_run(scenario))


def test_teacher_guard_switch_off_falls_back(monkeypatch):
    """开关关掉回到 legacy：越界题也不隔离（便于线上出问题一键回滚）。"""
    async def scenario(db):
        monkeypatch.setattr(kb_guard, "TEACHER_GUARD_ENABLED", False)
        res = await _create(db, [_payload(answer="H")])
        assert res["guard"]["enabled"] is False
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "teacher"

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- 批量导入
def _ingest_rows(raw_list, tenant=TENANT):
    rows = parse_records(raw_list)
    for row in rows:
        row.update(tenant_id=tenant, source_tag="import", status="draft")
    return rows


async def _upsert(db, rows):
    # sqlite 与 pg 的 insert 都支持 on_conflict_do_nothing，测试替身只为不连 PG
    return await ck.upsert_questions(rows, db)


def test_ingest_out_of_range_quarantined(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
        rows = _ingest_rows([{
            "subject": "数学", "grade": "高三", "type": "选择",
            "stem": "单选越界题：下列正确的是", "options": ["A. 1", "B. 2"],
            "answer": "H",
        }])
        report = await _upsert(db, rows)
        assert report["inserted"] == 1
        assert report["guard"]["chain"] == "import"
        assert report["guard"]["quarantine"] == 1

        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "quarantine"
        assert row.source_tag == "import:guard_quarantine"

    asyncio.run(_run(scenario))


def test_ingest_text_duplicate_quarantined(monkeypatch):
    """同一份 Excel 导两遍、只差标点时：第二行按指纹判重隔离。"""
    async def scenario(db):
        monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
        base = {
            "subject": "数学", "grade": "高三", "type": "选择",
            "stem": "设 z=1+i，则 z 的共轭复数为", "options": ["A. 1+i", "B. 1-i"],
            "answer": "B",
        }
        await _upsert(db, _ingest_rows([base]))
        report = await _upsert(db, _ingest_rows([
            {**base, "stem": "设 z=1+i, 则 z 的共轭复数为"}
        ]))
        assert report["inserted"] == 1
        assert report["guard"]["quarantine"] == 1
        assert "dedup_text" in report["guard"]["by_gate"]

    asyncio.run(_run(scenario))


def test_ingest_valid_row_keeps_import_tag(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
        rows = _ingest_rows([{
            "subject": "数学", "grade": "高三", "type": "填空",
            "stem": "若 a=2，求 a+1 的值", "answer": "3",
        }])
        report = await _upsert(db, rows)
        assert report["guard"]["quarantine"] == 0
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "import"

    asyncio.run(_run(scenario))


def test_ingest_guard_switch_off_falls_back(monkeypatch):
    async def scenario(db):
        monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
        monkeypatch.setattr(kb_guard, "IMPORT_GUARD_ENABLED", False)
        rows = _ingest_rows([{
            "subject": "数学", "grade": "高三", "type": "选择",
            "stem": "开关关闭时的越界题", "options": ["A. 1", "B. 2"], "answer": "H",
        }])
        report = await _upsert(db, rows)
        assert report["guard"]["enabled"] is False
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "import"

    asyncio.run(_run(scenario))


def test_ingest_reports_duplicated_hash_not_guarded(monkeypatch):
    """content_hash 命中唯一约束被跳过时不算新行，也不该进 guard 统计。"""
    async def scenario(db):
        monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
        raw = {
            "subject": "数学", "grade": "高三", "type": "填空",
            "stem": "重复导入幂等题", "answer": "1",
        }
        await _upsert(db, _ingest_rows([raw]))
        report = await _upsert(db, _ingest_rows([raw]))
        assert report["inserted"] == 0
        assert report["duplicated"] == 1
        assert report["guard"]["scanned"] == 0

    asyncio.run(_run(scenario))


def test_quarantine_persists_gate_and_reason():
    """隔离原因要落库（迁移 0025）：异常队列页靠它说明「为什么被挡」。

    只落 status/tag 的话，页面只能显示「已隔离」，人工无法判断该放行还是该归档。
    """
    async def scenario(db):
        res = await _create(db, [_payload(answer="H")])
        assert res["guard"]["quarantine"] == 1
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.guard_gate == "answer_out_of_range"
        assert row.guard_reason and "H" in row.guard_reason

    asyncio.run(_run(scenario))


# ---------------------------------------------------------------- 第一闸语义补盲
def _fake_semantic_hit(monkeypatch):
    """注入一条「改写式同题」命中，避免测试依赖真实 embedding 端点。"""
    async def fake(db, rows, **kwargs):
        hit = {
            "verdict": "manual_review", "gate": "dedup_semantic",
            "reason": "语义近似重复：与 #42 余弦 0.940", "dup_of": 42,
        }
        return {r.id: hit for r in rows}
    monkeypatch.setattr(kb_guard.semantic_dedup, "collect_semantic_matches", fake)


def test_teacher_semantic_hit_is_flagged_not_quarantined(monkeypatch):
    """人工链路 tag_manual=False，但语义近似仍必须打标进异常队列（不隔离）。"""
    _fake_semantic_hit(monkeypatch)

    async def scenario(db):
        res = await _create(db, [_payload()])
        assert res["created"] == 1
        assert res["guard"]["manual_review"] == 1
        assert res["guard"]["quarantine"] == 0
        assert "dedup_semantic" in res["guard"]["by_gate"]
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "teacher:guard_semantic_dup"
        assert row.guard_gate == "dedup_semantic"
        assert row.guard_dup_of == 42

    asyncio.run(_run(scenario))


def test_ingest_semantic_hit_is_flagged_not_quarantined(monkeypatch):
    """批量导入与教师录题同口径：语义命中打标、不隔离、dup_of 落库。"""
    monkeypatch.setattr(ck, "pg_insert", sqlite_insert)
    _fake_semantic_hit(monkeypatch)

    async def scenario(db):
        rows = _ingest_rows([{
            "subject": "数学", "grade": "高三", "type": "填空",
            "stem": "若 a=2，求 a+1 的值", "answer": "3",
        }])
        report = await _upsert(db, rows)
        assert report["guard"]["quarantine"] == 0
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "import:guard_semantic_dup"
        assert row.guard_dup_of == 42

    asyncio.run(_run(scenario))


def test_semantic_failure_never_blocks_entry(monkeypatch):
    """向量服务挂掉也要照常入库，其它闸口径不变。"""
    async def broken(rows, **kwargs):
        raise RuntimeError("embedding endpoint down")
    monkeypatch.setattr(kb_guard.semantic_dedup, "collect_semantic_matches", broken)

    async def scenario(db):
        res = await _create(db, [_payload()])
        assert res["created"] == 1
        row = (await db.execute(select(CourseQuestion))).scalars().one()
        assert row.status == "draft"
        assert row.source_tag == "teacher"

    asyncio.run(_run(scenario))
