"""T-C4 P2 埋点指标纯逻辑测试（不连库）。

覆盖六项指标构造事件流计算（含规避/挫败识别），以及 record_event 对新增
hint/intent 字段的落库持久化（FakeSession）。口径与计划书逐字对齐。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import learning, p2_metrics as svc

NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def _ev(
    student_id="s1",
    content_hash="h1",
    question="",
    event_type="solve",
    occurred_at=None,
    intent=None,
    hint_level=None,
    hint_frustration_streak=0,
    correct=None,
    knowledge_points=None,
    **extra,
):
    d = dict(
        student_id=student_id,
        content_hash=content_hash,
        question=question,
        event_type=event_type,
        occurred_at=occurred_at or NOW,
        intent=intent,
        hint_level=hint_level,
        hint_frustration_streak=hint_frustration_streak,
        correct=correct,
        knowledge_points=knowledge_points or [],
    )
    d.update(extra)
    return SimpleNamespace(**d)


# ---------------------------------------------------------------------------
# M1 首轮直给率
# ---------------------------------------------------------------------------
def test_first_round_direct_rate():
    events = [
        _ev(content_hash="hA", event_type="solve", intent="direct"),   # 系统首轮直给
        _ev(content_hash="hB", event_type="solve", intent="guide"),    # 引导档，不计
        _ev(content_hash="hC", event_type="direct_reveal"),            # 学生主动看解析，排除
    ]
    m = svc.compute_p2_metrics(events)
    # 分母=2（hA,hB）；分子=1（hA）。hC 被排除在系统会话之外
    assert m["first_round_direct_rate"] == 0.5
    assert m["_counts"]["system_sessions"] == 2
    assert m["_counts"]["first_give_sessions"] == 1


def test_first_round_direct_rate_empty_when_only_reveal():
    events = [_ev(content_hash="hC", event_type="direct_reveal")]
    m = svc.compute_p2_metrics(events)
    assert m["first_round_direct_rate"] is None


# ---------------------------------------------------------------------------
# M2 提示有效推进率 + M5 挫败信号率
# ---------------------------------------------------------------------------
def test_hint_progress_and_frustration():
    events = [
        _ev(content_hash="hD", event_type="hint_shown", hint_level=0, hint_frustration_streak=0, occurred_at=NOW),
        _ev(content_hash="hD", event_type="hint_shown", hint_level=1, hint_frustration_streak=0, occurred_at=NOW + timedelta(minutes=1)),  # 升级 → 推进
        _ev(content_hash="hD", event_type="hint_shown", hint_level=1, hint_frustration_streak=2, occurred_at=NOW + timedelta(minutes=2)),  # 挫败信号
        _ev(content_hash="hD", event_type="solve", correct=True, occurred_at=NOW + timedelta(minutes=3)),
    ]
    m = svc.compute_p2_metrics(events)
    # hint_rounds=3，effective=2（升级 + 正确 solve），frustration=1
    assert m["hint_effective_progress_rate"] == 2 / 3
    assert m["frustration_rate"] == 1 / 3
    assert m["_counts"]["hint_rounds"] == 3
    assert m["_counts"]["frustration_signals"] == 1


def test_hint_progress_explicit_event():
    events = [
        _ev(content_hash="hE", event_type="hint_shown", hint_level=2, occurred_at=NOW),
        _ev(content_hash="hE", event_type="hint_progress", occurred_at=NOW + timedelta(minutes=1)),
        _ev(content_hash="hE", event_type="solve", correct=False, occurred_at=NOW + timedelta(minutes=2)),
    ]
    m = svc.compute_p2_metrics(events)
    # 显式 hint_progress 计为推进；下一事件并未升级也未正确 → 仅 1 次有效
    assert m["hint_effective_progress_rate"] == 1 / 1
    assert m["frustration_rate"] == 0


# ---------------------------------------------------------------------------
# M3 单题平均交互轮次
# ---------------------------------------------------------------------------
def test_avg_rounds_per_question():
    events = [
        _ev(content_hash="hF", event_type="hint_shown", occurred_at=NOW),
        _ev(content_hash="hF", event_type="hint_shown", occurred_at=NOW + timedelta(minutes=1)),
        _ev(content_hash="hF", event_type="solve", occurred_at=NOW + timedelta(minutes=2)),
        _ev(content_hash="hG", event_type="solve", occurred_at=NOW),
    ]
    m = svc.compute_p2_metrics(events)
    assert m["avg_rounds_per_question"] == 2.0  # (3 + 1) / 2


# ---------------------------------------------------------------------------
# M4 规避/放弃率
# ---------------------------------------------------------------------------
def test_avoidance_and_abandonment():
    events = [
        _ev(content_hash="hG", event_type="hint_shown", occurred_at=NOW),          # 起了引导但离开 → 放弃
        _ev(content_hash="hH", event_type="direct_reveal", occurred_at=NOW),       # 看答案
        _ev(content_hash="hH", event_type="solve", correct=True, occurred_at=NOW + timedelta(minutes=5)),  # 又作答 → 规避
        _ev(content_hash="hI", event_type="hint_shown", occurred_at=NOW),          # 正常：引导后作答
        _ev(content_hash="hI", event_type="solve", correct=True, occurred_at=NOW + timedelta(minutes=5)),
    ]
    m = svc.compute_p2_metrics(events)
    # 3 个会话：hG 放弃，hH 规避，hI 正常
    assert m["avoidance_rate"] == 1 / 3
    assert m["abandonment_rate"] == 1 / 3
    assert m["avoidance_abandonment_rate"] == 2 / 3


# ---------------------------------------------------------------------------
# M6 延迟迁移正确率
# ---------------------------------------------------------------------------
def test_delayed_retention_rate():
    events = [
        _ev(content_hash="hJ", event_type="solve", correct=True, knowledge_points=["导数"], occurred_at=NOW),
        _ev(content_hash="hJ", event_type="solve", correct=True, knowledge_points=["导数"], occurred_at=NOW + timedelta(days=10)),
        _ev(content_hash="hJ", event_type="solve", correct=False, knowledge_points=["导数"], occurred_at=NOW + timedelta(days=10)),
        # 另一个知识点只有首次作答，不计入延迟样本
        _ev(content_hash="hK", event_type="solve", correct=True, knowledge_points=["积分"], occurred_at=NOW),
    ]
    m = svc.compute_p2_metrics(events)
    # 延迟样本 = [True, False] → 0.5
    assert m["delayed_retention_rate"] == 0.5
    assert m["_counts"]["delayed_solves"] == 2


def test_delayed_retention_none_without_gap():
    events = [
        _ev(content_hash="hL", event_type="solve", correct=True, knowledge_points=["x"], occurred_at=NOW),
        _ev(content_hash="hL", event_type="solve", correct=False, knowledge_points=["x"], occurred_at=NOW + timedelta(days=2)),  # <7天
    ]
    m = svc.compute_p2_metrics(events)
    assert m["delayed_retention_rate"] is None


# ---------------------------------------------------------------------------
# 聚合结构完整性（API 返回形状）
# ---------------------------------------------------------------------------
def test_metrics_keys_present():
    events = [
        _ev(content_hash="hM", event_type="solve", intent="direct"),
        _ev(content_hash="hN", event_type="hint_shown", hint_level=0),
        _ev(content_hash="hN", event_type="direct_reveal"),
    ]
    m = svc.compute_p2_metrics(events)
    for key in (
        "first_round_direct_rate",
        "hint_effective_progress_rate",
        "avg_rounds_per_question",
        "avoidance_rate",
        "abandonment_rate",
        "avoidance_abandonment_rate",
        "frustration_rate",
        "delayed_retention_rate",
        "_counts",
    ):
        assert key in m


def test_red_lines():
    metrics = {
        "first_round_direct_rate": 0.10,   # > 5% → 超线
        "hint_effective_progress_rate": 0.50,  # < 60% → 超线
        "avoidance_abandonment_rate": 0.20,    # <= 25% → 不超
        "frustration_rate": 0.10,             # <= 15% → 不超
    }
    flags = svc.evaluate_red_lines(metrics)
    assert flags["first_round_direct_rate"]["breached"] is True
    assert flags["hint_effective_progress_rate"]["breached"] is True
    assert flags["avoidance_abandonment_rate"]["breached"] is False
    assert flags["frustration_rate"]["breached"] is False


# ---------------------------------------------------------------------------
# record_event 落库持久化（新增 hint/intent 字段）
# ---------------------------------------------------------------------------
class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


def test_record_event_persists_hint_fields():
    payload = {
        "event_type": "hint_shown",
        "student_id": "s99",
        "content_hash": "h99",
        "ts": "2026-10-23T12:00:00+00:00",
        "intent": "guide",
        "intent_source": "rules",
        "hint_level": 3,
        "hint_level_name": "L3",
        "hint_rounds": 4,
        "hint_max_rounds": 4,
        "hint_step": 2,
        "hint_step_total": 3,
        "hint_frustration_streak": 2,
        "hint_finale": True,
        "hint_direct_reveal": False,
        "question_tier": "finale",
        "question_tier_name": "压轴",
        "knowledge_points": ["导数"],
    }
    session = _FakeSession()
    obj = asyncio.run(learning.record_event(session, payload))
    assert obj.hint_level == 3
    assert obj.hint_frustration_streak == 2
    assert obj.intent == "guide"
    assert obj.intent_source == "rules"
    assert obj.question_tier == "finale"
    assert obj.hint_finale is True
    assert obj.hint_direct_reveal is False
    assert obj.knowledge_points == ["导数"]
    # 未被消费端丢弃：字段确实写进了对象
    assert obj in session.added


# ---------------------------------------------------------------------------
# API 端到端（TestClient + 依赖覆盖，不连真实 DB）
# ---------------------------------------------------------------------------
def test_api_p2_metrics_returns_six_metrics():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import metrics
    from app.core.db import get_db
    from app.models.ops import StudentEvent

    rows = [
        StudentEvent(
            student_id="s1", content_hash="hA", event_type="solve",
            intent="direct", occurred_at=NOW,
        ),
        StudentEvent(
            student_id="s1", content_hash="hB", event_type="solve",
            intent="guide", occurred_at=NOW,
        ),
    ]

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return rows

    class _FakeAsyncSession:
        async def execute(self, *a, **k):
            return _FakeResult()

    app_local = FastAPI()
    app_local.include_router(metrics.router, prefix="/api/admin")
    app_local.dependency_overrides[get_db] = lambda: _FakeAsyncSession()

    client = TestClient(app_local)
    resp = client.get("/api/admin/p2-metrics", headers={"X-User-Role": "teacher", "X-User-Id": "t1"})
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "first_round_direct_rate",
        "hint_effective_progress_rate",
        "avg_rounds_per_question",
        "avoidance_rate",
        "abandonment_rate",
        "avoidance_abandonment_rate",
        "frustration_rate",
        "delayed_retention_rate",
    ):
        assert key in body
    # 与纯函数口径一致：s1 系统会话 2 个，首轮直给 1 个
    assert body["first_round_direct_rate"] == 0.5
    assert "red_lines" in body
    assert body["red_lines"]["first_round_direct_rate"]["breached"] is True  # 50% > 5%
