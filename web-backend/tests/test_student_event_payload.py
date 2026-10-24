"""S3 ⑤ 联调：agent-backend judge v2 载荷 -> web-backend 消费端。

测试均为纯逻辑（不连数据库、不触 Kafka），见 conftest 说明。
"""
import asyncio
from datetime import datetime, timezone

from app.services import learning
from app.models.ops import StudentEvent, StudentStepError


# ------------------------------------------------------------------ 夹具

def judge_payload(**over) -> dict:
    """agent-backend /solve 上报的判分事件（含 judge v2 扩展载荷）。"""
    base = {
        "event_type": "solve",
        "student_id": "s1",
        "tenant_id": "t1",
        "subject": "数学",
        "mode": "explain",
        "question": "解方程 2x=4",
        "knowledge_points": ["一元二次不等式"],
        "content_hash": "h_single",
        "correct": False,
        "partial": False,
        "first_wrong_step": 1,
        "error_type": "calc",
        "attribution_source": "map",
        "evidence": "学生选 A（x=1）",
        "ts": "2026-10-24T01:00:00+00:00",
    }
    base.update(over)
    return base


class _FakeSession:
    """只承接 add/commit/refresh 的假 session（SQLAlchemy 的 add 是同步方法）。"""

    def __init__(self):
        self.added = []
        self.committed = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        return obj


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------- 事件流水落库字段

def test_record_event_persists_judge_v2_fields():
    db = _FakeSession()
    obj = _run(learning.record_event(db, judge_payload()))

    assert isinstance(obj, StudentEvent)
    assert obj.content_hash == "h_single"
    assert obj.correct is False
    assert obj.partial is False
    assert obj.first_wrong_step == 1
    assert obj.error_type == "calc"
    assert obj.attribution_source == "map"
    assert obj.evidence == "学生选 A（x=1）"
    assert obj.occurred_at == datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)
    assert db.committed == 1


def test_record_event_keeps_unjudged_as_none():
    """未提交作答时 correct 是 None，绝不能被当成 False（否则误记错题）。"""
    db = _FakeSession()
    obj = _run(learning.record_event(db, judge_payload(correct=None, error_type=None)))
    assert obj.correct is None
    assert obj.error_type is None


def test_record_event_coerces_step_no_from_string():
    db = _FakeSession()
    obj = _run(learning.record_event(db, judge_payload(first_wrong_step="3")))
    assert obj.first_wrong_step == 3


def test_record_event_ignores_garbage_step_no():
    db = _FakeSession()
    obj = _run(learning.record_event(db, judge_payload(first_wrong_step="abc")))
    assert obj.first_wrong_step is None


# ------------------------------------------------- 步骤级错因流水

def test_extract_step_error_full():
    row = learning.extract_step_error(judge_payload())
    assert row is not None
    assert row["student_id"] == "s1"
    assert row["content_hash"] == "h_single"
    assert row["step_no"] == 1
    assert row["error_type"] == "calc"
    assert row["attribution_source"] == "map"


def test_extract_step_error_requires_question_and_type():
    """没有题目 hash 或错因类型就不落库（避免产生无法归属/无意义的流水）。"""
    assert learning.extract_step_error(judge_payload(content_hash="")) is None
    assert learning.extract_step_error(judge_payload(error_type=None)) is None
    assert learning.extract_step_error({}) is None
    assert learning.extract_step_error("not a dict") is None


def test_extract_step_error_keeps_unknown_for_review():
    """unknown 也要落：它是人工复核队列的计数口径（S6 要求 unknown ≤10%）。"""
    row = learning.extract_step_error(judge_payload(error_type="unknown",
                                                    attribution_source="unknown"))
    assert row is not None
    assert row["error_type"] == "unknown"


def test_record_step_error_writes_row():
    db = _FakeSession()
    ok = _run(learning.record_step_error(db, judge_payload()))
    assert ok is True
    assert isinstance(db.added[0], StudentStepError)
    assert db.added[0].error_type == "calc"


def test_record_step_error_skips_when_incomplete():
    db = _FakeSession()
    ok = _run(learning.record_step_error(db, judge_payload(error_type=None)))
    assert ok is False
    assert db.added == []


# ------------------------------------------------- 向后兼容

def test_legacy_payload_without_attribution_still_works():
    """旧版事件（无 judge v2 字段）不能被消费端打挂。"""
    legacy = {
        "event_type": "solve",
        "student_id": "s2",
        "question": "旧事件",
        "knowledge_points": ["集合"],
    }
    db = _FakeSession()
    obj = _run(learning.record_event(db, legacy))
    assert obj.error_type is None
    assert obj.correct is None
    assert _run(learning.record_step_error(db, legacy)) is False
