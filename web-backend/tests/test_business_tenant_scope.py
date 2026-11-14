"""平台占位租户归一 + 学情看板 region_admin 权限（2026-11-14）。

线上根因：超管 JWT 的 tenant_id 是占位值 ``platform``，业务表里没有任何行；
``grading.py::_tenant`` 原来写 ``user.tenant_id or FALLBACK_TENANT``，占位值
非空所以不会被兜底，/grading/tickets、/grading/metrics 对 admin 全部查空
（库里 7 份提交 / 94 条 item 其实都在 school_pilot_01）。

同时 region_admin 的「学情看板」菜单可见，/analytics/summary 却只放行
admin/teacher/parent，点进去必 403。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import analytics, grading
from app.core.db import get_db
from app.core.security import (
    FALLBACK_TENANT,
    PLATFORM_TENANT,
    business_tenant_id,
    read_tenant_id,
    write_tenant_id,
)

PILOT = "school_pilot_01"


def _headers(role: str, tenant: str | None = None) -> dict:
    h = {"X-User-Id": "u_test", "X-User-Role": role}
    if tenant is not None:
        h["X-Tenant-Id"] = tenant
    return h


# ---------------------------------------------------------------- 纯函数：三工具分工
def test_business_tenant_id_平台占位归一且不返回None():
    assert business_tenant_id({"role": "admin", "tenant_id": PLATFORM_TENANT}) == FALLBACK_TENANT
    assert business_tenant_id({"role": "region_admin", "tenant_id": PLATFORM_TENANT}) == FALLBACK_TENANT
    # 网关漏注入租户头时也归一到同一个兜底值
    assert business_tenant_id({"role": "admin", "tenant_id": None}) == FALLBACK_TENANT
    assert business_tenant_id({"role": "teacher", "tenant_id": ""}) == FALLBACK_TENANT
    # 真实租户原样保留，不能被误改成兜底租户
    assert business_tenant_id({"role": "teacher", "tenant_id": "demo"}) == "demo"
    assert business_tenant_id({"role": "region_admin", "tenant_id": PILOT}) == PILOT


def test_三个租户工具各管一段():
    admin = {"role": "admin", "tenant_id": PLATFORM_TENANT}
    assert read_tenant_id(admin) is None            # 列表接口：跨租户只读
    assert write_tenant_id(admin) == PLATFORM_TENANT  # 写侧：严格占位
    assert business_tenant_id(admin) == FALLBACK_TENANT  # service 层：绝不 None

    teacher = {"role": "teacher", "tenant_id": "demo"}
    assert read_tenant_id(teacher) == "demo"
    assert write_tenant_id(teacher) == "demo"
    assert business_tenant_id(teacher) == "demo"


def test_grading_使用归一后的业务租户():
    assert grading._tenant({"role": "admin", "tenant_id": PLATFORM_TENANT}) == PILOT
    assert grading._tenant({"role": "admin", "tenant_id": None}) == PILOT
    assert grading._tenant({"role": "school", "tenant_id": "demo"}) == "demo"


# ---------------------------------------------------------------- 学情看板权限
@pytest.fixture
def analytics_client():
    app = FastAPI()
    app.include_router(analytics.router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_summary_region_admin_可访问(analytics_client):
    r = analytics_client.get(
        "/api/admin/analytics/summary?student_id=s1",
        headers=_headers("region_admin", PILOT),
    )
    assert r.status_code == 200, r.text
    assert r.json()["student_id"] == "s1"


def test_summary_school_仍403(analytics_client):
    r = analytics_client.get(
        "/api/admin/analytics/summary?student_id=s1",
        headers=_headers("school", "demo"),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------- grading 读侧租户
class _EmptyResult:
    def scalar(self): return 0
    def scalar_one_or_none(self): return None
    def scalar_one(self): return None
    def scalars(self): return self
    def all(self): return []
    def __iter__(self): return iter([])


class _CaptureDB:
    """收集 SQL 绑定参数，断言租户条件里没有 platform 占位。"""

    def __init__(self):
        self.stmts = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _EmptyResult()

    @property
    def params(self):
        out = []
        for stmt in self.stmts:
            try:
                out.extend(stmt.compile().params.values())
            except Exception:  # noqa: BLE001
                pass
        return out


@pytest.fixture
def grading_client():
    app = FastAPI()
    app.include_router(grading.router, prefix="/api/admin")
    db = _CaptureDB()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()


def test_admin_工单列表按兜底租户过滤而不是platform(grading_client):
    client, db = grading_client
    r = client.get("/api/admin/grading/tickets", headers=_headers("admin", PLATFORM_TENANT))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0
    assert PILOT in db.params, db.params
    assert PLATFORM_TENANT not in db.params, "平台占位不该进业务表 where 条件"


def test_school_只按自己的租户过滤(grading_client):
    client, db = grading_client
    r = client.get("/api/admin/grading/tickets", headers=_headers("school", "demo"))
    assert r.status_code == 200, r.text
    assert "demo" in db.params, db.params
    assert PILOT not in db.params


def test_region_admin_批改仍403_与撤菜单的产品决策一致(grading_client):
    client, _ = grading_client
    r = client.get("/api/admin/grading/tickets", headers=_headers("region_admin", PILOT))
    assert r.status_code == 403


def test_admin_指标看板传入兜底租户(monkeypatch, grading_client):
    client, _ = grading_client
    seen = {}

    async def fake_collect(db, tenant_id, days=None, paper_id=None):
        seen["tenant_id"] = tenant_id
        return {
            "tenant_id": tenant_id, "window_days": days, "generated_at": "2026-11-14T00:00:00+00:00",
            "items": {"total": 94, "by_status": {}, "auto_gradable": 0, "gradable_total": 0},
            "metrics": {"coverage": {}, "manual_rate": {}, "accuracy": {}},
            "sources": [], "tickets": {}, "revisions": {}, "freezes": {}, "by_paper": [],
        }

    monkeypatch.setattr(grading.grading_metrics, "collect", fake_collect)
    r = client.get("/api/admin/grading/metrics", headers=_headers("admin", PLATFORM_TENANT))
    assert r.status_code == 200, r.text
    assert seen["tenant_id"] == PILOT
    assert r.json()["tenant_id"] == PILOT
