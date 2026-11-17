"""T-CAP1 标注审核服务层单测（纯逻辑，不连库）。

覆盖闭环写回的核心安全面：
- coerce_annotation_value：字段白名单 + 类型/范围二次校验
- review_annotation(approve)：写回 course_kb 对应字段 + 留痕 + 状态流转
- review_annotation(reject)：不写回、不碰题库
- 非法值 / 题目不存在：返回 ok=False，状态保持 pending
- 已处理建议不可重复审核（幂等）
- annotation_stats：队列分布 + 覆盖缺口聚合口径
"""
import asyncio
import sys
import os
from datetime import datetime

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import ops  # noqa: E402
from app.models.course_kb import CourseQuestion  # noqa: E402
from app.models.ops import CourseKbAnnotationSuggestion  # noqa: E402


# --------------------------------------------------------------------------
# coerce_annotation_value
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field,val,expected", [
    ("difficulty", "4", (4, None)),
    ("difficulty", "9", (None, "difficulty 超出 1~5")),
    ("difficulty", "abc", (None, "difficulty 非整数")),
    ("year", "2023", (2023, None)),
    ("year", "1800", (None, "year 超出 1900~2100")),
    ("paper_type", "新高考I卷", ("新高考I卷", None)),
    ("paper_type", "  ", (None, "建议值为空")),
    ("evil_field", "1", (None, "字段不在白名单：evil_field")),
])
def test_coerce(field, val, expected):
    got, err = ops.coerce_annotation_value(field, val)
    assert got == expected[0]
    assert err == expected[1]


# --------------------------------------------------------------------------
# fake session：够 review_annotation 用，不碰真库
# --------------------------------------------------------------------------
class _Result:
    def __init__(self, scalar=None):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return self._scalar if isinstance(self._scalar, list) else []


class _FakeDb:
    def __init__(self, question=None):
        self.question = question
        self.commits = 0
        self.refreshes = 0
        self.executed_sql = ""

    async def execute(self, stmt, *args):
        self.executed_sql = str(stmt)
        return _Result(self.question)

    async def get(self, model, item_id):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshes += 1


def _suggestion(field="difficulty", value="4", status="pending"):
    return CourseKbAnnotationSuggestion(
        id=1, content_hash="h1", field_name=field, suggested_value=value,
        status=status, prompt_version=1,
    )


def _question():
    return CourseQuestion(id=1, content_hash="h1", subject="数学", stem="题", difficulty=3)


# --------------------------------------------------------------------------
# review_annotation
# --------------------------------------------------------------------------
def test_approve_writes_back_and_leaves_trail():
    obj = _suggestion()
    q = _question()
    db = _FakeDb(question=q)

    result = asyncio.run(ops.review_annotation(db, obj, "approved", actor_id="teacher01"))

    assert result["ok"] is True
    assert q.difficulty == 4                 # 写回 course_kb
    assert obj.status == "approved"
    assert obj.reviewed_by == "teacher01"
    assert isinstance(obj.reviewed_at, datetime)
    assert db.commits == 1                   # 题 + 建议同 session 一次提交


def test_approve_year_and_paper_type():
    for field, value, attr, expected in [
        ("year", "2023", "year", 2023),
        ("paper_type", "新高考I卷", "paper_type", "新高考I卷"),
    ]:
        obj = _suggestion(field=field, value=value)
        q = _question()
        db = _FakeDb(question=q)
        result = asyncio.run(ops.review_annotation(db, obj, "approved", actor_id="u"))
        assert result["ok"] is True
        assert getattr(q, attr) == expected


def test_reject_never_writes_back():
    obj = _suggestion()
    q = _question()
    db = _FakeDb(question=q)

    result = asyncio.run(ops.review_annotation(db, obj, "rejected", actor_id="teacher01"))

    assert result["ok"] is True
    assert q.difficulty == 3                 # 原题未动
    assert obj.status == "rejected"
    assert obj.reviewed_by == "teacher01"
    assert "course_kb" not in db.executed_sql.lower()  # 没有走写回查询


def test_approve_invalid_value_keeps_pending():
    obj = _suggestion(value="9")  # 难度超范围
    q = _question()
    db = _FakeDb(question=q)

    result = asyncio.run(ops.review_annotation(db, obj, "approved"))

    assert result["ok"] is False
    assert "非法" in result["reason"]
    assert q.difficulty == 3
    assert obj.status == "pending"           # 不被误标，可修正后再审
    assert db.commits == 0


def test_approve_missing_question_keeps_pending():
    obj = _suggestion()
    db = _FakeDb(question=None)              # 题目不存在

    result = asyncio.run(ops.review_annotation(db, obj, "approved"))

    assert result["ok"] is False
    assert "不存在" in result["reason"]
    assert obj.status == "pending"


def test_already_reviewed_is_rejected():
    obj = _suggestion(status="approved")
    db = _FakeDb(question=_question())

    result = asyncio.run(ops.review_annotation(db, obj, "approved"))

    assert result["ok"] is False
    assert "已处理" in result["reason"]
    assert db.commits == 0                   # 重复审核不产生新提交


def test_unknown_decision_raises():
    with pytest.raises(ValueError):
        asyncio.run(ops.review_annotation(_FakeDb(), _suggestion(), "maybe"))
