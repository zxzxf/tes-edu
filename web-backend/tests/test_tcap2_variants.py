"""T-CAP2 变式生成器单测（纯逻辑，不连库/不联网）。

覆盖 DoD 关键面：
- 稳健 JSON 解析（裸 JSON / 围栏 / 前言尾注 / 单对象 / 非法）
- prompt 构造（源题注入、盲解 prompt 绝不带入答案——红线）
- 答案等价比对（数值/分数/根式/区间/选项/多解；null 不通过）
- is_numeric_gradeable 分流（数值可判 vs 证明/文字转人工）
- content_hash 与 parser.py 同口径（幂等去重）
- 同 KP 强制（变式继承源题知识点，不凭空产题）
"""
import asyncio
import hashlib
import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
import gen_variants as gv  # noqa: E402


# ---------------------------------------------------------------- parse_items
def test_parse_plain_items():
    raw = '{"items":[{"source_id":"1","stem":"变式题干","answer":"3"}]}'
    out = gv.parse_items(raw, id_key="source_id")
    assert out["1"]["stem"] == "变式题干"
    assert out["1"]["answer"] == "3"


def test_parse_fenced_with_preamble():
    raw = '好的：\n```json\n{"items":[{"id":"v1","answer":"sqrt(2)"}]}\n```\ndone'
    out = gv.parse_items(raw)
    assert out["v1"]["answer"] == "sqrt(2)"


def test_parse_bare_array():
    raw = '[{"id":"v1","answer":"A"}]'
    assert gv.parse_items(raw)["v1"]["answer"] == "A"


def test_parse_invalid_escape_repaired():
    """模型输出含 LaTeX \\( 非法转义时，清洗后仍能解析（真实踩坑：10 批挂 1 批）。"""
    raw = r'{"items":[{"id":"v1","stem":"已知 \(x+1\)^2","answer":"3"}]}'
    out = gv.parse_items(raw)
    assert out["v1"]["stem"] == r"已知 \(x+1\)^2"
    assert out["v1"]["answer"] == "3"


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        gv.parse_items("完全不是 JSON")


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        gv.parse_items("")


# ---------------------------------------------------------------- prompt（红线：盲解不带答案）
def test_generate_prompt_has_source():
    rows = [{"id": 7, "stem": "已知 a1=1 求通项", "type": "填空", "answer": "n",
             "options": None, "analysis": "等差", "knowledge_points": ["数列"]}]
    prompt = gv.build_generate_prompt(rows, 2)
    assert "每道题生成变式数】2" in prompt
    assert "已知 a1=1" in prompt
    assert "原题答案=n" in prompt  # A 生成需要看原题答案（保证换数后答案已知）


def test_solve_prompt_never_leaks_answer():
    """红线：LLM-B 盲解 prompt 绝不能出现 A 的答案或原题答案。"""
    variants = [{
        "id": "7v1", "stem": "已知 b1=2 求通项", "type": "填空", "options": None,
        "answer": "2n", "analysis": "A 的解析含答案 2n",  # 这些都不该进盲解 prompt
    }]
    prompt = gv.build_solve_prompt(variants)
    assert "已知 b1=2" in prompt
    assert "2n" not in prompt, "盲解 prompt 泄漏了 LLM-A 的答案"
    assert "A 的解析" not in prompt


def test_clip_long_stem():
    assert len(gv._clip("x" * 1000, 600)) <= 601


def test_chat_parsed_retries_on_bad_json():
    """LLM 首次输出坏 JSON，重试后成功（偶发输出故障容错）。"""
    calls = {"n": 0}

    async def flaky_transport(system, user, *, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Unterminated string at char 1029 {坏输出"
        return '{"items":[{"id":"v1","answer":"3"}]}'

    out = asyncio.run(gv._chat_parsed("sys", "usr", "id", flaky_transport, 90.0))
    assert calls["n"] == 2
    assert out["v1"]["answer"] == "3"


def test_chat_parsed_gives_up_after_retries():
    """连续坏输出时重试耗尽后抛出，由调用方计入失败。"""
    async def always_bad(system, user, *, timeout):
        return "完全不是 JSON"

    with pytest.raises(ValueError):
        asyncio.run(gv._chat_parsed("sys", "usr", "id", always_bad, 90.0))


# ---------------------------------------------------------------- content_hash
def test_content_hash_matches_parser():
    stem, qtype, answer = "已知 f(x)=x²，求 f(2)", "填空", "4"
    expected = hashlib.sha256(f"{stem}|{qtype}|{answer}".encode("utf-8")).hexdigest()
    assert gv.content_hash(stem, qtype, answer) == expected


def test_content_hash_differs_on_answer():
    h1 = gv.content_hash("题干", "填空", "3")
    h2 = gv.content_hash("题干", "填空", "4")
    assert h1 != h2, "换数后答案变了，变式必须有独立 hash"


# ---------------------------------------------------------------- answers_equivalent
def test_equiv_exact_string():
    ok, _ = gv.answers_equivalent({"options": None}, "3", "3")
    assert ok


def test_equiv_fraction_decimal():
    ok, verdict = gv.answers_equivalent({"options": None}, "61/25", "2.44")
    assert ok and "numeric" in verdict


def test_equiv_root():
    ok, _ = gv.answers_equivalent({"options": None}, "sqrt(2)", "1.4142135")
    assert ok


def test_equiv_interval():
    ok, _ = gv.answers_equivalent({"options": None}, "[2,3)", "[2,3)")
    assert ok


def test_choice_by_label():
    v = {"options": ["A.1", "B.2", "C.3", "D.4"]}
    ok, _ = gv.answers_equivalent(v, "C", "C")
    assert ok


def test_choice_by_content():
    v = {"options": ["A.1", "B.2", "C.3", "D.4"]}
    ok, _ = gv.answers_equivalent(v, "3", "C")
    assert ok, "A 写选项内容、B 写字母也应判等价"


def test_choice_mismatch():
    v = {"options": ["A.1", "B.2", "C.3", "D.4"]}
    ok, _ = gv.answers_equivalent(v, "C", "D")
    assert not ok


def test_numeric_mismatch():
    ok, _ = gv.answers_equivalent({"options": None}, "3", "4")
    assert not ok


def test_null_answer_not_equivalent():
    ok, _ = gv.answers_equivalent({"options": None}, "3", None)
    assert not ok


def test_non_numeric_unverified_not_pass():
    """证明题/文字答案：equiv 无法判定 → 不通过（转 manual，绝不凭自述入库）。"""
    ok, verdict = gv.answers_equivalent({"options": None}, "略证", "显然成立")
    assert not ok
    assert "unverified" in verdict


# ---------------------------------------------------------------- is_numeric_gradeable
def test_gradeable_numbers():
    assert gv.is_numeric_gradeable("3", None)
    assert gv.is_numeric_gradeable("61/25", None)
    assert gv.is_numeric_gradeable("[2,3)", None)
    assert gv.is_numeric_gradeable("2 或 -2", None)


def test_gradeable_choice():
    assert gv.is_numeric_gradeable("C", ["A.1", "B.2", "C.3", "D.4"])


def test_not_gradeable_text():
    assert not gv.is_numeric_gradeable("证明略", None)
    assert not gv.is_numeric_gradeable("x 是偶数", None)
    assert not gv.is_numeric_gradeable(None, None)


# ---------------------------------------------------------------- 落库映射（同 KP / 审核隔离）
def test_insert_pass_maps_to_draft_variant_auto():
    """自动通过 → draft + variant_auto，且继承源题 KP/subject（同 KP 红线）。"""
    import json as _json
    captured = {}

    class FakeConn:
        async def execute(self, sql, *params):
            captured["sql"] = sql
            captured["params"] = params

    src = {"subject": "数学", "grade": "高三", "type": "填空", "difficulty": 3,
           "knowledge_points": ["KP-函数"], "content_hash": "abc123"}
    variant = {"stem": "变式题干", "answer": "4", "analysis": "解析",
               "type": "填空", "content_hash": "def456"}
    status, tag = asyncio.run(gv.insert_variant(FakeConn(), variant, src, "pass", "A=4 B=4 numeric_equal"))
    assert (status, tag) == ("draft", "variant_auto")
    p = captured["params"]
    assert p[0] == "数学" and p[2] == "填空"          # 继承源题 subject/type
    assert _json.loads(p[8]) == ["KP-函数"]          # 继承源题 KP（同 KP 红线）
    assert p[9] == "def456"                          # 变式独立 hash
    assert "variant_of:abc123" in p[13]              # 记录来源源题


def test_insert_fail_maps_to_quarantine():
    """校验失败 → quarantine + variant_quarantine，绝不进 draft。"""
    captured = {}

    class FakeConn:
        async def execute(self, sql, *params):
            captured["params"] = params

    src = {"subject": "数学", "type": "填空", "knowledge_points": [],
           "content_hash": "abc", "grade": None, "difficulty": None}
    variant = {"stem": "坏变式", "answer": None, "type": "填空", "content_hash": "bad1"}
    status, tag = asyncio.run(gv.insert_variant(FakeConn(), variant, src, "fail", "A vs B 不等"))
    assert (status, tag) == ("quarantine", "variant_quarantine")
    assert captured["params"][12] == "quarantine"


def test_insert_manual_maps_to_draft_manual_review():
    """非数值类 → draft + variant_manual_review，强制人工复核，不自动发布。"""
    captured = {}

    class FakeConn:
        async def execute(self, sql, *params):
            captured["params"] = params

    src = {"subject": "数学", "type": "解答", "knowledge_points": [],
           "content_hash": "abc", "grade": None, "difficulty": None}
    variant = {"stem": "证明题变式", "answer": "略证", "type": "解答", "content_hash": "man1"}
    status, tag = asyncio.run(gv.insert_variant(FakeConn(), variant, src, "manual", "非数值"))
    assert (status, tag) == ("draft", "variant_manual_review")
    assert "变式校验" in captured["params"][6]  # analysis 追加校验说明供人工看
