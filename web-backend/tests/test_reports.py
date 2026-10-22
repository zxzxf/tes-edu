"""家长端周报纯逻辑测试（不连库）。

覆盖 _build_report 的薄弱点推导/平均掌握度/学习投入，以及 _render_html 的自包含渲染。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import reports as svc


def _m(kp, correct=0, wrong=0, subject="数学", practice=None, last=None):
    return SimpleNamespace(
        knowledge_point=kp,
        subject=subject,
        correct_count=correct,
        wrong_count=wrong,
        practice_count=practice if practice is not None else correct + wrong,
        last_practiced_at=last,
    )


def _e(occurred_at, event_type="solve", subject="数学", question="", kps=None):
    return SimpleNamespace(
        occurred_at=occurred_at,
        event_type=event_type,
        subject=subject,
        question=question,
        knowledge_points=kps or [],
    )


NOW = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)


def test_build_report_basic():
    masteries = [
        _m("函数", correct=8, wrong=2),   # 0.80 -> 已掌握
        _m("数列", correct=2, wrong=6),    # 0.25 -> 薄弱
        _m("几何", correct=0, wrong=0),    # None（仅练习无判分）
    ]
    events = [
        _e(NOW - timedelta(days=2)),       # 周期内
        _e(NOW - timedelta(days=20)),      # 周期外（>7天）
    ]
    r = svc._build_report("stu_1", masteries, events, period_days=7, now=NOW)

    assert r["student_id"] == "stu_1"
    assert r["total_knowledge_points"] == 3
    # 平均掌握度 = (0.8 + 0.25) / 2 = 0.525
    assert r["avg_mastery"] == 0.525
    assert r["mastered_count"] == 1
    assert r["weak_count"] == 1
    assert r["weak_points"][0]["knowledge_point"] == "数列"
    # 学习投入仅统计周期内事件
    assert r["study"]["active_days"] == 1
    assert r["study"]["action_count"] == 1
    assert r["study"]["est_minutes"] == svc.EST_MIN_PER_ACTION
    assert r["suggestions"]
    assert "3 个知识点" in r["summary"]


def test_build_report_empty():
    r = svc._build_report("stu_x", [], [], period_days=7, now=NOW)
    assert r["avg_mastery"] is None
    assert r["mastered_count"] == 0
    assert r["weak_count"] == 0
    assert r["study"]["active_days"] == 0
    # 无练习时应提示家长鼓励练习
    assert any("暂无练习记录" in s for s in r["suggestions"])


def test_render_html_self_contained_and_escaped():
    masteries = [
        _m("函数", correct=8, wrong=2),
        _m("<script>alert(1)</script>", correct=2, wrong=6),  # 需被转义
    ]
    events = [_e(NOW - timedelta(days=1), question="求 f(x)=x^2 的导数", kps=["函数"])]
    r = svc._build_report("stu_1", masteries, events, period_days=7, now=NOW)
    html = svc._render_html(r)

    assert "<!doctype html>" in html.lower()
    assert "学情周报" in html
    assert "stu_1" in html
    # 危险内容必须被转义，不能原样出现
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    # 掌握度条应渲染
    assert "width:" in html
