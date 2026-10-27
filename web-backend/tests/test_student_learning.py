"""学生端学习资产：SM-2 排程口径 / 越权边界 / 路由挂载（不连库、不发网络请求）。

App 端有同口径的 `Sm2Scheduler`，两侧各有一份实现（客户端离线也能排程），
所以这里的用例是**契约锁**：任何一侧改了公式，另一侧的单测都会红。
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api import student_learning as api
from app.services import drill as drill_svc
from main import app

NOW = datetime(2026, 10, 28, 3, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- SM-2

def test_forget_resets_interval_and_lower_ease():
    """rating<3（忘了）：间隔归 1 天、lapses+1、ease-0.2。"""
    s = drill_svc.sm2_schedule(2.5, 10.0, 3, 0, rating=0, now=NOW)
    assert s.interval_days == 1.0
    assert s.lapses == 1
    assert abs(s.ease - 2.3) < 1e-6
    assert s.due_at == NOW.replace() or s.due_at > NOW


def test_ease_has_floor():
    """ease 下限 1.3：连忘多次也不会把间隔压成负数或无限密。"""
    ease = 2.5
    for _ in range(12):
        ease = drill_svc.sm2_schedule(ease, 6.0, 3, 0, rating=0, now=NOW).ease
    assert ease == 1.3


def test_interval_ladder_1_6_then_multiply():
    """及格后间隔：首次 1 天 → 第二次 6 天 → 之后 ×ease。"""
    s1 = drill_svc.sm2_schedule(2.5, 0.0, 0, 0, rating=4, now=NOW)
    assert s1.interval_days == 1.0 and s1.reps == 1

    s2 = drill_svc.sm2_schedule(s1.ease, s1.interval_days, s1.reps, 0, rating=4, now=NOW)
    assert s2.interval_days == 6.0 and s2.reps == 2

    s3 = drill_svc.sm2_schedule(s2.ease, s2.interval_days, s2.reps, 0, rating=4, now=NOW)
    assert s3.reps == 3
    assert abs(s3.interval_days - round(6.0 * s2.ease, 4)) < 1e-6


def test_easy_rating_raises_ease_more():
    """4 分维持 ease 不变（标准 SM-2 语义：答对不额外变简单），5 分才上调。"""
    e4 = drill_svc.sm2_schedule(2.5, 6.0, 3, 0, rating=4, now=NOW).ease
    e5 = drill_svc.sm2_schedule(2.5, 6.0, 3, 0, rating=5, now=NOW).ease
    assert e4 == 2.5
    assert e5 > 2.5


def test_interval_capped():
    """间隔上限 365 天：长期满分也不会排到几年后。"""
    s = drill_svc.sm2_schedule(2.5, 300.0, 20, 0, rating=5, now=NOW)
    assert s.interval_days <= 365.0


# --------------------------------------------------------------------------- 越权

def test_student_ignores_requested_student_id():
    """学生角色强制用网关注入的 user_id——传别人的学号也不生效。"""
    sid = api._student_id({"user_id": "u_self", "role": "student"}, "student_other")
    assert sid == "u_self"


def test_teacher_may_query_others():
    """教师/管理员可以显式指定 student_id 查别人。"""
    sid = api._student_id({"user_id": "u_teacher", "role": "teacher"}, "student_003")
    assert sid == "student_003"


def test_teacher_without_param_falls_back_to_self():
    assert api._student_id({"user_id": "u_teacher", "role": "teacher"}, "") == "u_teacher"


# --------------------------------------------------------------------------- 路由

def _paths() -> set:
    out = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        if path and path.startswith("/learning"):
            out.add(path)
    return out


def test_learning_endpoints_mounted_at_root():
    """网关对 /learning/ 原样转发，这几个端点必须在根路径可达。"""
    paths = _paths()
    for p in (
        "/learning/wrong-questions",
        "/learning/wrong-questions/sync",
        "/learning/wrong-questions/stats",
        "/learning/wrong-questions/{wrong_id}",
        "/learning/drill-cards",
        "/learning/drill-cards/stats",
        "/learning/drill-cards/{card_id}/review",
    ):
        assert p in paths, f"{p} 未挂载，网关转发会 404"


def test_missing_identity_headers_rejected():
    """没有网关注入的身份头 → 401（不允许匿名读写错题）。"""
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/learning/wrong-questions", params={"student_id": "student_003"})
    assert r.status_code == 401


def test_student_role_can_reach_endpoint_structure():
    """学生身份请求能通过鉴权层（DB 依赖被覆盖，只验证到路由与鉴权）。

    这里不校验返回内容——数据库依赖被替换成假实现，目的只是确认
    「学生角色 + 身份头」不会被 403 拦下。
    """
    from app.core.db import get_db

    class _FakeDB:  # 只满足依赖注入占位，不会被真正查询
        pass

    async def _fake_get_db():
        yield _FakeDB()

    app.dependency_overrides[get_db] = _fake_get_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get(
            "/learning/wrong-questions",
            params={"student_id": "student_other"},
            headers={"X-User-Id": "u_self", "X-User-Role": "student"},
        )
        # 403 表示角色被拒（不该发生）；500 来自假 DB，属预期
        assert r.status_code != 403, r.text
    finally:
        app.dependency_overrides.pop(get_db, None)
