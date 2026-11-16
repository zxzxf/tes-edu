"""T-CAP1 标注自动化管线单测（纯逻辑，不连库/不联网）。

覆盖 DoD 关键面：
- 稳健 JSON 解析（裸 JSON / 围栏 / 前言尾注 / id 对齐 / 非法输出）
- 建议值转型（难度 1~5、年份范围、卷别非空；不合法返回 None 不写建议）
- prompt 构造（字段/题目注入，题干截断，system 不混入数据）
- 审核隔离：write_suggestions 只产出 pending 行，不触碰 course_kb
- 续跑幂等：同 (hash, field) 第二次跳过
"""
import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
import auto_annotate as aa  # noqa: E402


# --------------------------------------------------------------------------
# parse_suggestion
# --------------------------------------------------------------------------
def test_parse_plain_json():
    raw = '{"items":[{"id":"1","difficulty":4,"year":2023,"paper_type":"新高考I卷"}]}'
    out = aa.parse_suggestion(raw)
    assert out["1"]["difficulty"] == 4
    assert out["1"]["year"] == 2023


def test_parse_fenced_with_preamble():
    raw = '好的，结果如下：\n```json\n{"items":[{"id":"abc","difficulty":2,"reason":"基础题"}]}\n```\n希望有帮助。'
    out = aa.parse_suggestion(raw)
    assert out["abc"]["difficulty"] == 2
    assert out["abc"]["reason"] == "基础题"


def test_parse_bare_array():
    raw = '[{"id":"x","paper_type":"全国乙卷"}]'
    out = aa.parse_suggestion(raw)
    assert out["x"]["paper_type"] == "全国乙卷"


def test_parse_skips_items_without_id():
    raw = '{"items":[{"difficulty":5},{"id":"ok","difficulty":1}]}'
    out = aa.parse_suggestion(raw)
    assert set(out) == {"ok"}


@pytest.mark.parametrize("bad", [
    "",
    "not json at all",
    '{"foo": 3, "bar": 4}',
])
def test_parse_invalid_raises(bad):
    with pytest.raises(ValueError):
        aa.parse_suggestion(bad)


def test_parse_object_items_is_tolerated_as_empty():
    """items 非数组时容错成空（不写任何建议），不抛异常也不误写。"""
    assert aa.parse_suggestion('{"items": {"difficulty": 3}}') == {}


# --------------------------------------------------------------------------
# coerce_value
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field,raw,expected", [
    ("difficulty", 4, "4"),
    ("difficulty", "5", "5"),
    ("difficulty", 6, None),
    ("difficulty", 0, None),
    ("difficulty", "high", None),
    ("difficulty", None, None),
    ("year", 2023, "2023"),
    ("year", 1999, "1999"),      # 边界内合法
    ("year", 1800, None),        # 超出 1900~2100
    ("year", 2200, None),
    ("year", "2021年", None),    # 非纯整数不猜
    ("paper_type", "  新高考I卷  ", "新高考I卷"),
    ("paper_type", "null", None),
    ("paper_type", "", None),
    ("paper_type", None, None),
])
def test_coerce_value(field, raw, expected):
    assert aa.coerce_value(field, raw) == expected


# --------------------------------------------------------------------------
# build_user_prompt
# --------------------------------------------------------------------------
def test_build_prompt_includes_fields_and_ids():
    rows = [{"id": "7", "stem": "题干内容", "analysis": "解析", "subject": "数学", "type": "单选", "grade": "高三"}]
    user = aa.build_user_prompt(rows, ["difficulty", "year"])
    assert "difficulty, year" in user
    assert "id=7" in user
    assert "题干内容" in user
    assert "题干内容" not in aa.SYSTEM_PROMPT  # 具体题目数据不拼进 system


def test_build_prompt_truncates_long_stem():
    rows = [{"id": "9", "stem": "长" * 1000, "analysis": "", "subject": "数学"}]
    user = aa.build_user_prompt(rows, ["difficulty"])
    assert "…" in user
    assert len(user) < 1000


# --------------------------------------------------------------------------
# write_suggestions：审核隔离 + 幂等（用最小 fake db）
# --------------------------------------------------------------------------
class _FakeDb:
    """模拟 asyncpg conn：只记录 executemany 收到的行，不碰真库。"""

    def __init__(self):
        self.executed = []

    async def executemany(self, sql, rows):
        self.executed.extend(rows)


def test_write_suggestions_pending_only_and_idempotent():
    import asyncio
    rows = [
        {"id": "1", "content_hash": "h1", "subject": "数学"},
        {"id": "2", "content_hash": "h2", "subject": "数学"},
    ]
    parsed = {
        "1": {"difficulty": 4, "year": 2023, "reason": "含年份"},
        "2": {"difficulty": 2, "year": None, "reason": "未知年份"},
    }
    db = _FakeDb()
    already = {("h1", "difficulty")}  # h1 难度已有建议，续跑应跳过

    async def scenario():
        return await aa.write_suggestions(db, parsed, rows, ["difficulty", "year"], already)

    stats = asyncio.run(scenario())

    assert stats["skipped_existing"] == 1    # h1.difficulty 已有建议
    assert stats["no_llm_value"] == 1        # 题2 的 year=null，不写
    assert stats["written"] == 2             # h1.year(2023) + h2.difficulty(2)
    assert len(db.executed) == 2
    # 审核隔离：只 INSERT 建议表（值为 pending），不 UPDATE course_kb
    # 行结构 = (content_hash, field_name, suggested_value, reason, source_model, prompt_version)
    for row in db.executed:
        assert row[1] in ("difficulty", "year")
        assert row[5] == aa.PROMPT_VERSION


def test_write_suggestions_unknown_id_counted_invalid():
    import asyncio
    db = _FakeDb()

    async def scenario():
        return await aa.write_suggestions(
            db, {"ghost": {"difficulty": 3}}, [{"id": "1", "content_hash": "h1"}],
            ["difficulty"], set())

    stats = asyncio.run(scenario())
    assert stats["invalid"] == 1
    assert len(db.executed) == 0
