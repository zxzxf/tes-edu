"""T-D3 LLM 用量聚合与预算告警测试（纯逻辑 + 接口冒烟，不连库）。"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import llm_usage as api
from app.core.db import get_db
from app.services import llm_usage as svc

NOW = datetime(2026, 10, 25, 3, 0, 0, tzinfo=timezone.utc)


def _row(
    model="qwen3.8-flash",
    mode="explain",
    grade="single",
    prompt_tokens=100,
    completion_tokens=50,
    degraded=False,
    cache_hit=False,
    error_kind=None,
    cost=0.0005,
    latency_ms=120,
    created_at=None,
):
    return SimpleNamespace(
        model=model, mode=mode, grade=grade, subject="数学",
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_ms=latency_ms, degraded=degraded, cache_hit=cache_hit,
        error_kind=error_kind, cost=cost, created_at=created_at or NOW,
    )


# ---------------------------------------------------------------------------
# 聚合口径
# ---------------------------------------------------------------------------
def test_total_aggregation():
    rows = [
        _row(),                                    # 正常调用
        _row(degraded=True, error_kind="quota"),   # 降级
        _row(cache_hit=True, cost=0.0),            # 缓存命中
    ]
    out = svc.compute_llm_usage(rows)
    t = out["total"]
    assert t["calls"] == 3
    assert t["real_calls"] == 2          # 缓存命中不算真实调用
    assert t["cache_hits"] == 1
    assert t["degraded"] == 1
    # 缓存命中行不计入 token 消耗与延迟均值
    assert t["prompt_tokens"] == 200
    assert t["completion_tokens"] == 100
    assert t["cache_hit_rate"] == 0.3333  # 1/3 保留 4 位
    assert t["degraded_rate"] == 0.5     # 降级率按真实调用算分母
    assert t["errors"]["quota"] == 1
    assert t["avg_latency_ms"] == 120.0


def test_empty_rows_no_crash():
    out = svc.compute_llm_usage([])
    assert out["total"]["calls"] == 0
    assert out["total"]["degraded_rate"] is None
    assert out["total"]["cache_hit_rate"] is None
    assert out["by_model"] == {} and out["by_day"] == {}


def test_group_by_model_and_day():
    rows = [
        _row(model="a", created_at=NOW),
        _row(model="b", created_at=NOW - timedelta(days=1), cost=0.002),
        _row(model="a", created_at=NOW - timedelta(days=1)),
    ]
    out = svc.compute_llm_usage(rows)
    assert set(out["by_model"]) == {"a", "b"}
    assert out["by_model"]["a"]["calls"] == 2
    assert len(out["by_day"]) == 2
    # 当日成本取"最近一天"
    assert out["budget"]["used"] == pytest_approx(out["by_day"][max(out["by_day"])]["cost"])


def pytest_approx(v):  # 本地小工具，避免额外依赖
    return round(float(v), 6)


def test_degraded_rate_helper():
    rows = [_row(degraded=True), _row(), _row(cache_hit=True)]
    assert svc.degraded_rate(rows) == 0.5
    assert svc.degraded_rate([_row(cache_hit=True)]) is None


# ---------------------------------------------------------------------------
# 预算判定
# ---------------------------------------------------------------------------
def test_budget_states(monkeypatch):
    monkeypatch.setenv("LLM_DAILY_BUDGET", "100")
    monkeypatch.setenv("LLM_BUDGET_WARN_RATIO", "0.8")
    monkeypatch.setenv("LLM_BUDGET_ACTION", "alert")

    assert svc.evaluate_budget(50.0)["state"] == "ok"
    assert svc.evaluate_budget(80.0)["state"] == "warn"
    assert svc.evaluate_budget(120.0)["state"] == "over"
    assert svc.evaluate_budget(120.0)["force_simple"] is False
    assert svc.evaluate_budget(120.0, action="force_simple")["force_simple"] is True
    assert svc.evaluate_budget(10.0, budget=0)["state"] == "disabled"


def test_budget_uses_env_defaults(monkeypatch):
    monkeypatch.delenv("LLM_DAILY_BUDGET", raising=False)
    cfg = svc.budget_config()
    assert cfg["budget"] == 50.0 and cfg["action"] == "alert"


# ---------------------------------------------------------------------------
# 接口冒烟
# ---------------------------------------------------------------------------
def test_api_llm_usage_smoke(monkeypatch):
    monkeypatch.setenv("LLM_DAILY_BUDGET", "100")
    rows = [
        _row(),
        _row(degraded=True, error_kind="quota"),
        _row(cache_hit=True, cost=0.0),
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
    app_local.include_router(api.router, prefix="/api/admin")
    app_local.dependency_overrides[get_db] = lambda: _FakeAsyncSession()

    client = TestClient(app_local)
    resp = client.get(
        "/api/admin/llm-usage", headers={"X-User-Role": "teacher", "X-User-Id": "t1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"]["calls"] == 3
    assert body["total"]["degraded_rate"] == 0.5
    assert "budget" in body and "by_model" in body

    # 预算子接口（当日成本远低于 100 → ok）
    resp2 = client.get(
        "/api/admin/llm-usage/budget", headers={"X-User-Role": "admin", "X-User-Id": "a1"}
    )
    assert resp2.status_code == 200
    assert resp2.json()["state"] == "ok"


def test_api_llm_usage_requires_role():
    app_local = FastAPI()
    app_local.include_router(api.router, prefix="/api/admin")
    app_local.dependency_overrides[get_db] = lambda: None
    client = TestClient(app_local)
    # 无角色头 → 401/403（由 require_role 决定），不得是 200
    resp = client.get("/api/admin/llm-usage")
    assert resp.status_code in (401, 403)
