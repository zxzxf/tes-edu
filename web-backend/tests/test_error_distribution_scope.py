"""班级错因分布（A 方案）回归：权限对齐菜单、租户锁死、窗口归零提示。

2026-11-14 线上现象：region_admin 菜单看得到「班级错因分布」，接口却
``require_role(ADMIN, TEACHER)`` 直接 403；前端没有 catch，错误被伪装成
空列表。同时 ``tenant_id`` 参数直接透传，teacher 不传参数就能聚合全库
（39 条里只有 38 条属本租户）、显式传参更能读别校数据。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.analytics import router, scope_tenant
from app.core.db import get_db
from app.core.security import get_current_user
from app.services import learning


def _user(role: str, tenant_id: str) -> dict:
    return {"user_id": "u_test", "role": role, "tenant_id": tenant_id}


@pytest.fixture
def api(monkeypatch):
    """只挂 analytics 路由 + 假 DB，避免碰真实库/外部服务。"""
    app = FastAPI()
    app.include_router(router)
    calls = []

    async def fake_list_step_errors(db, **kw):
        calls.append(kw)
        if kw.get("limit") == 1:
            # hint 查询：该范围还有一条 120 天前的老记录
            return [SimpleNamespace(
                occurred_at=datetime.now(timezone.utc) - timedelta(days=120),
                student_id="s_old",
            )]
        return []

    monkeypatch.setattr(learning, "list_step_errors", fake_list_step_errors)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: _user(
        "region_admin", "school_pilot_01"
    )

    def as_user(role, tenant_id):
        app.dependency_overrides[get_current_user] = lambda: _user(role, tenant_id)

    with TestClient(app) as c:
        yield c, as_user, calls
    app.dependency_overrides.clear()


def test_scope_tenant_纯函数():
    # 真平台级：不传 = 跨租户（None），可显式指定范围
    assert scope_tenant(_user("admin", "platform"), None) is None
    assert scope_tenant(_user("region_admin", "platform"), "demo") == "demo"
    # 带真实租户的 region_admin（试点校 pilot）：锁本租户，不看别校/platform
    assert scope_tenant(_user("region_admin", "school_pilot_01"), None) == "school_pilot_01"
    assert scope_tenant(_user("region_admin", "school_pilot_01"), "demo") == "school_pilot_01"
    # 租户角色：请求参数一律无效
    assert scope_tenant(_user("teacher", "demo"), "school_pilot_01") == "demo"
    assert scope_tenant(_user("teacher", "demo"), None) == "demo"
    assert scope_tenant(_user("school", "demo"), "platform") == "demo"
    # 网关漏注入租户头时按试点校兜底，绝不退化成跨租户
    assert scope_tenant(_user("teacher", None), None) == "school_pilot_01"


def test_region_admin_可访问且按本租户统计(api):
    client, _, calls = api
    r = client.get("/analytics/error-distribution")
    assert r.status_code == 200, r.text
    assert calls[0]["tenant_id"] == "school_pilot_01"
    body = r.json()
    assert body["scope"]["tenant_id"] == "school_pilot_01"
    assert body["total"] == 0


def test_默认窗口放宽到90天(api):
    client, _, _ = api
    body = client.get("/analytics/error-distribution").json()
    assert body["scope"]["period_days"] == 90


def test_窗口内无数据时给出放宽周期的提示(api):
    client, _, calls = api
    body = client.get("/analytics/error-distribution?period_days=30").json()
    hint = body["scope"].get("hint")
    assert hint and "周期调到" in hint and "天以上" in hint
    # hint 是回看查询，不能把 120 天前的老数据算进本次统计
    assert body["total"] == 0
    assert len(calls) == 2 and calls[1]["limit"] == 1
    assert "since" not in calls[1]


def test_teacher_显式传别校tenant无效(api):
    client, as_user, calls = api
    as_user("teacher", "demo")
    r = client.get("/analytics/error-distribution?tenant_id=school_pilot_01")
    assert r.status_code == 200, r.text
    assert calls[0]["tenant_id"] == "demo"
    assert r.json()["scope"]["tenant_id"] == "demo"


def test_平台超管可跨租户并指定范围(api):
    client, as_user, calls = api
    as_user("admin", "platform")
    r = client.get("/analytics/error-distribution")
    assert r.status_code == 200, r.text
    assert calls[0]["tenant_id"] is None
    assert r.json()["scope"]["tenant_id"] is None

    r2 = client.get("/analytics/error-distribution?tenant_id=school_pilot_01")
    assert r2.status_code == 200, r2.text
    assert calls[-2]["tenant_id"] == "school_pilot_01"


def test_school_仍然403(api):
    client, as_user, _ = api
    as_user("school", "demo")
    r = client.get("/analytics/error-distribution")
    assert r.status_code == 403
    assert r.json()["detail"] == "insufficient role"
