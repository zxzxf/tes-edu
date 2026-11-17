"""T-CAP4 第一闸补盲：embedding 语义去重。

锁定四条红线：
1. 单靠余弦不够——必须数值签名 + 标答等价双过滤（防换数变式/同学科异题误杀）；
2. 语义命中只 manual 打标、不隔离，且不能被闸2/闸3 的 pass 吞掉；
3. 人工链路 tag_manual=False 时语义仍打标（要进异常队列）；
4. embedding/检索失败一律降级为空，不抛穿、不阻塞入库。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.core.intake_guard import (
    GATE_SEMANTIC, TAG_SEMANTIC, VERDICT_MANUAL, VERDICT_PASS, VERDICT_QUARANTINE,
    judge_item, number_signature, same_answer_for_dedup, semantic_dup_check,
    verdict_to_fields,
)
from app.services import semantic_dedup as sd


def _row(stem="已知函数 f(x)=x^2-2x-3，求 f(x) 的最小值", answer="-4", qtype="填空"):
    return {"stem": stem, "answer": answer, "type": qtype, "options": None}


def _match(sim=0.94, other=None):
    return {
        "similarity": sim, "threshold": 0.90,
        "other": other or {"id": 42, "stem": _row()["stem"], "answer": "-4", "type": "填空"},
    }


# ---------------------------------------------------------------- 复合过滤
def test_number_signature_keeps_variants_distinct():
    a = number_signature("已知 x^2-2x-3，求最小值")
    b = number_signature("已知 x^2-5x+6，求最小值")
    c = number_signature("已知x²−2x−3，求最小值")
    assert a == c and a != b


def test_answer_equivalence_handles_numeric_and_choice():
    assert same_answer_for_dedup(_row(answer="-4.0"), {"answer": "-4", "type": "填空"})
    assert not same_answer_for_dedup(_row(), {"answer": "3", "type": "填空"})
    choice = _row(qtype="选择", answer="B")
    assert same_answer_for_dedup(choice, {"answer": "b", "type": "选择"})
    assert not same_answer_for_dedup(choice, {"answer": "D", "type": "选择"})


def test_semantic_check_threshold_and_filters():
    assert semantic_dup_check(_row(), match=_match(0.93))["gate"] == GATE_SEMANTIC
    assert semantic_dup_check(_row(), match=_match(0.89)) is None
    assert semantic_dup_check(_row(), match=None) is None
    # 换数变式：余弦再高也不判重
    variant = {"id": 7, "stem": "已知函数 f(x)=x^2-5x+6，求 f(x) 的最小值", "answer": "-4", "type": "填空"}
    assert semantic_dup_check(_row(), match=_match(0.99, variant)) is None
    # 数字相同但标答不同：异题
    other_answer = {"id": 7, "stem": _row()["stem"], "answer": "9", "type": "填空"}
    assert semantic_dup_check(_row(), match=_match(0.99, other_answer)) is None


def test_semantic_hit_carries_dup_of():
    hit = semantic_dup_check(_row(), match=_match())
    assert hit["verdict"] == VERDICT_MANUAL
    assert hit["dup_of"] == 42
    assert "#42" in hit["reason"] and "0.940" in hit["reason"]


# ---------------------------------------------------------------- 裁定优先级
def test_semantic_beats_pass_but_not_quarantine():
    row = {"stem": "解方程 x^2-2x-3=0，求x", "answer": "3", "type": "填空",
           "options": None, "content_hash": "h-pass"}
    match = {"similarity": 0.95, "threshold": 0.90,
             "other": {"id": 42, "stem": row["stem"], "answer": "3", "type": "填空"}}
    out = judge_item(row, known_hashes=set(), known_fps=set(), semantic_match=match)
    assert out["verdict"] == VERDICT_MANUAL and out["gate"] == GATE_SEMANTIC
    assert any(c["gate"] == "mechanical_pass" for c in out["checks"])

    quarantined = judge_item(
        row, known_hashes={"h-pass"}, known_fps=set(), semantic_match=match,
    )
    assert quarantined["verdict"] == VERDICT_QUARANTINE
    assert quarantined["gate"] == "dedup_exact"


def test_no_semantic_match_keeps_legacy_verdict():
    row = {"stem": "解方程 x^2-2x-3=0，求x", "answer": "3", "type": "填空",
           "options": None, "content_hash": "h-legacy"}
    assert judge_item(row, known_hashes=set(), known_fps=set())["verdict"] == VERDICT_PASS


# ---------------------------------------------------------------- 写回口径
def test_semantic_tag_written_even_when_manual_tag_disabled():
    fields = verdict_to_fields(
        VERDICT_MANUAL, base_tag="teacher", tag_manual=False,
        gate=GATE_SEMANTIC, reason="语义近似", dup_of=42,
    )
    assert fields["source_tag"] == f"teacher:{TAG_SEMANTIC}"
    assert fields["guard_gate"] == GATE_SEMANTIC
    assert fields["guard_dup_of"] == 42
    # 普通 manual 仍按人工链路口径不打标（不能借语义闸把噪音放进来）
    assert verdict_to_fields(VERDICT_MANUAL, base_tag="teacher", tag_manual=False) == {}


def test_quarantine_keeps_quarantine_tag_even_with_dup_of():
    fields = verdict_to_fields(
        VERDICT_QUARANTINE, base_tag="auto_crawl", gate="dedup_text", dup_of=7,
    )
    assert fields["source_tag"] == "auto_crawl:guard_quarantine"
    assert fields["guard_dup_of"] == 7


# ---------------------------------------------------------------- 检索层降级
def test_vector_literal_and_validation():
    assert sd.vector_literal([0.1, -2.0]) == "[0.1,-2]"
    assert sd.vector_ok([0.0] * 1024)
    assert not sd.vector_ok([0.0] * 10)
    assert not sd.vector_ok([float("nan")] * 1024)


def test_embed_failure_degrades_to_none(monkeypatch):
    async def scenario():
        async def fake(*args, **kwargs):
            return {"ok": False, "vectors": None, "reason": "未配置 embedding 端点"}
        monkeypatch.setattr(sd.embed_svc, "embed_texts", fake)
        rows = [SimpleNamespace(stem="题干", analysis="")]
        assert await sd.embed_rows(rows) == [None]
    asyncio.run(scenario())


def test_collect_semantic_matches_filters_and_maps(monkeypatch):
    async def scenario():
        monkeypatch.setattr(sd, "SEMANTIC_ENABLED", True)
        near = {"id": 42, "stem": _row()["stem"], "answer": "-4", "type": "填空", "sim": 0.94}
        far = {"id": 43, "stem": "完全不同的另一道题", "answer": "8", "type": "填空", "sim": 0.4}

        class FakeResult:
            def all(self):
                return [(r["id"], r["stem"], r["answer"], r["type"], r["sim"]) for r in (near, far)]

        class FakeDB:
            async def execute(self, stmt, params):
                assert ":vec" not in str(stmt) or "CAST(:vec AS vector)" in str(stmt)
                return FakeResult()

        rows = [SimpleNamespace(id=1, stem=_row()["stem"], answer="-4", type="填空",
                                options=None, analysis="")]
        vec = [0.01] * 1024
        out = await sd.collect_semantic_matches(FakeDB(), rows, vectors=[vec])
        assert out[1]["dup_of"] == 42
        assert out[1]["gate"] == GATE_SEMANTIC
        # 回填 course_kb.embedding：否则新题永远进不了后续语义召回
        assert rows[0].embedding == vec
    asyncio.run(scenario())


def test_collect_semantic_matches_can_skip_vector_persist(monkeypatch):
    async def scenario():
        monkeypatch.setattr(sd, "SEMANTIC_ENABLED", True)

        class FakeDB:
            async def execute(self, stmt, params):
                class R:
                    def all(self):
                        return []
                return R()

        rows = [SimpleNamespace(id=1, stem="题", answer="1", type="填空",
                                options=None, analysis="")]
        vec = [0.01] * 1024
        await sd.collect_semantic_matches(
            FakeDB(), rows, vectors=[vec], persist_vectors=False,
        )
        assert not hasattr(rows[0], "embedding")
    asyncio.run(scenario())


def test_collect_semantic_matches_degrades_on_db_error(monkeypatch):
    async def scenario():
        monkeypatch.setattr(sd, "SEMANTIC_ENABLED", True)

        class BrokenDB:
            async def execute(self, stmt, params):
                raise RuntimeError("pgvector unavailable")

        rows = [SimpleNamespace(id=1, stem="题", answer="1", type="填空",
                                options=None, analysis="")]
        out = await sd.collect_semantic_matches(
            BrokenDB(), rows, vectors=[[0.01] * 1024],
        )
        assert out == {}
    asyncio.run(scenario())


def test_asyncpg_variant_filters(monkeypatch):
    async def scenario():
        monkeypatch.setattr(sd, "SEMANTIC_ENABLED", True)
        near = {"id": 42, "stem": _row()["stem"], "answer": "-4", "type": "填空", "sim": 0.94}

        class FakeConn:
            async def fetch(self, sql, *params):
                assert "$1::vector" in sql and "$2" in sql
                return [near]

        rows = [{**_row(), "id": 1, "analysis": ""}]
        out = await sd.collect_semantic_matches_asyncpg(
            FakeConn(), rows, vectors=[[0.01] * 1024],
        )
        assert out[1]["dup_of"] == 42
    asyncio.run(scenario())
