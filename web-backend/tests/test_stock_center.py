"""股票盯盘业务中心回归（docx/股票盯盘业务中心实施计划.md v1.4 验收锁）。

sqlite 内存真库 + TestClient（惯例同 test_push_hub）：
- provider 纯解析/归一（新浪样本行、symbol 市场判定）
- engine.judge 四条件判定（纯函数）
- CRUD 权限：admin 200 / teacher 403；非法 token/condition 422
- quotes 读路径：monkeypatch 源，Redis 缺席直返源数据（降级即被测路径）
- push-default 两级解析 + push-test 走真实 emit（渠道未配→dead 不炸）
- worker tick_once 注入 now_fn 绕时段闸门：拉源→快照→触达→冷却二次不触发
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("PUSH_PORTAL_SECRET", "test-portal-secret-not-for-prod")

from app.core.config import settings  # noqa: E402


@pytest.fixture()
def env():
    settings.push_portal_secret = "test-portal-secret-not-for-prod"
    settings.stock_worker_enabled = False  # 测试不起 worker，tick 单独手动调
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    from app.models.course_kb import Base
    import app.models.push_hub  # noqa: F401
    import app.models.stock_models  # noqa: F401

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as s:
            yield s

    async def bootstrap():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(bootstrap())

    import app.core.db as db_mod

    db_mod.engine = engine
    db_mod.SessionLocal = factory

    from main import app as fastapi_app

    fastapi_app.dependency_overrides[db_mod.get_db] = override_get_db
    with TestClient(fastapi_app) as c:
        yield c, factory
    fastapi_app.dependency_overrides.clear()


ADMIN = {"X-User-Id": "admin01", "X-User-Role": "admin", "X-Tenant-Id": "platform"}
TEACHER = {"X-User-Id": "t1", "X-User-Role": "teacher", "X-Tenant-Id": "school_pilot_01"}

FRIEND_TK = "a" * 32
FRIEND_TK2 = "b" * 32


def _q(price=1700.0, pct=2.5, prev=1658.0, name="贵州茅台"):
    return {"symbol": "sh600519", "name": name, "price": price, "prev_close": prev,
            "open": prev, "high": price, "low": prev, "volume": 1e6, "amount": 1e9,
            "pct": pct, "change": price - prev, "quote_time": "2026-11-20 10:00:00"}


# ---------------------------------------------------------------- 纯函数
def test_normalize_symbol():
    from app.services.stock.provider import normalize_symbol

    assert normalize_symbol("600519") == "sh600519"
    assert normalize_symbol("000858") == "sz000858"
    assert normalize_symbol("300750") == "sz300750"
    assert normalize_symbol("SH601318") == "sh601318"
    assert normalize_symbol("830799") == "bj830799"
    assert normalize_symbol("usAAPL") is None
    assert normalize_symbol("abc") is None


def test_parse_sina_line():
    from app.services.stock.provider import _parse_sina

    # 新浪真实行 33 字段：前 10 有效数值 + 五档 20 字段 + 日期,时间,状态
    bidask = ",".join(f"{i}.00" for i in range(20))
    line = ('var hq_str_sh600519="贵州茅台,1650.000,1658.000,1700.000,1710.000,'
            f'1645.000,1699.900,1700.000,30000000,50000000000,{bidask},'
            '2026-11-20,15:00:00,00";')
    q = _parse_sina("sh600519", line)
    assert q and q["name"] == "贵州茅台"
    assert q["price"] == 1700.0
    assert abs(q["pct"] - 2.53) < 0.01  # (1700-1658)/1658
    assert q["quote_time"] == "2026-11-20 15:00:00"


def test_judge_four_conditions():
    from app.models.stock_models import StockAlert
    from app.services.stock.engine import judge

    a = lambda c, t: StockAlert(symbol="sh600519", condition=c, threshold=t)
    assert judge(a("price_above", 1700), _q(price=1700))
    assert not judge(a("price_above", 1701), _q(price=1700))
    assert judge(a("price_below", 1600), _q(price=1599))
    assert not judge(a("price_below", 1600), _q(price=1650))
    assert judge(a("pct_up", 2.0), _q(pct=2.5))
    assert not judge(a("pct_up", 3.0), _q(pct=2.5))
    assert judge(a("pct_down", 2.0), _q(pct=-2.5))
    assert not judge(a("pct_down", 2.0), _q(pct=2.5))
    assert not judge(a("pct_up", 1), _q(price=0))  # 停牌零价不触发
    assert not judge(a("unknown_cond", 1), _q())


# ---------------------------------------------------------------- CRUD 与权限
def test_watchlist_crud_and_rbac(env):
    client, _ = env
    from app.services.stock import provider

    async def fake_fetch(symbols, timeout=6.0):
        return {s: _q() for s in symbols}

    provider.fetch_quotes = fake_fetch

    r = client.post("/api/stock/watchlist", json={"symbol": "600519"}, headers=ADMIN)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert r.json()["name"] == "贵州茅台"

    # 重复 409
    assert client.post("/api/stock/watchlist", json={"symbol": "sh600519"},
                       headers=ADMIN).status_code == 409
    # 非法 symbol 422
    assert client.post("/api/stock/watchlist", json={"symbol": "usAAPL"},
                       headers=ADMIN).status_code == 422

    r = client.get("/api/stock/watchlist", headers=ADMIN)
    assert r.status_code == 200 and len(r.json()["items"]) == 1

    r = client.patch(f"/api/stock/watchlist/{wid}", json={"pinned": True}, headers=ADMIN)
    assert r.json()["pinned"] is True

    # 非 admin 403
    assert client.get("/api/stock/watchlist", headers=TEACHER).status_code == 403
    assert client.post("/api/stock/watchlist", json={"symbol": "600519"},
                       headers=TEACHER).status_code == 403

    assert client.delete(f"/api/stock/watchlist/{wid}", headers=ADMIN).status_code == 200


def test_alert_validation(env):
    client, _ = env
    from app.services.stock import provider

    async def fake_fetch(symbols, timeout=6.0):
        return {s: _q() for s in symbols}

    provider.fetch_quotes = fake_fetch

    bad = {"symbol": "600519", "condition": "price_go_up", "threshold": 1}
    assert client.post("/api/stock/alerts", json=bad, headers=ADMIN).status_code == 422
    bad2 = {"symbol": "600519", "condition": "price_above", "threshold": 1,
            "receiver_tokens": ["not-a-32hex-token"]}
    assert client.post("/api/stock/alerts", json=bad2, headers=ADMIN).status_code == 422
    ok = {"symbol": "600519", "condition": "price_above", "threshold": 1690,
          "receiver_tokens": [FRIEND_TK]}
    r = client.post("/api/stock/alerts", json=ok, headers=ADMIN)
    assert r.status_code == 201, r.text
    aid = r.json()["alert_id"]
    r = client.patch(f"/api/stock/alerts/{aid}", json={"cooldown_minutes": 5}, headers=ADMIN)
    assert r.json()["cooldown_minutes"] == 5
    assert client.get("/api/stock/alerts", headers=ADMIN).json()["items"][0]["alert_id"] == aid


def test_friends_and_push_default(env):
    client, _ = env
    r = client.post("/api/stock/friends", json={"alias": "老王", "token": FRIEND_TK.upper()},
                    headers=ADMIN)
    assert r.status_code == 201, r.text
    fid = r.json()["friend_id"]
    # 重复令牌 409
    assert client.post("/api/stock/friends", json={"alias": "老王2", "token": FRIEND_TK},
                       headers=ADMIN).status_code == 409
    items = client.get("/api/stock/friends", headers=ADMIN).json()["items"]
    assert items[0]["token_mask"] == "aaaa…aaaa"

    # friends 模式必须带令牌
    assert client.put("/api/stock/push-default", json={"mode": "friends", "tokens": []},
                      headers=ADMIN).status_code == 422
    r = client.put("/api/stock/push-default", json={"mode": "friends", "tokens": [FRIEND_TK]},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["mode"] == "friends"
    assert client.get("/api/stock/push-default", headers=ADMIN).json()["tokens"] == [FRIEND_TK]

    assert client.delete(f"/api/stock/friends/{fid}", headers=ADMIN).status_code == 200


def test_resolve_receivers_two_level(env):
    client, factory = env
    from app.services.stock.push import resolve_receivers

    async def run():
        async with factory() as db:
            # alert 覆盖优先
            assert await resolve_receivers(db, [FRIEND_TK2], {"mode": "friends", "tokens": [FRIEND_TK]}) == [FRIEND_TK2]
            # 无覆盖 → 全局默认 friends
            assert await resolve_receivers(db, [], {"mode": "friends", "tokens": [FRIEND_TK]}) == [FRIEND_TK]
            # 默认 self / 空 → SELF
            assert await resolve_receivers(db, [], {"mode": "self"}) == ["SELF"]
            assert await resolve_receivers(db, [], None) == ["SELF"]

    asyncio.run(run())


def test_quotes_degrade_no_500(env):
    """源全挂（fetch 抛异常）→ 空列表 degraded，绝不 500。"""
    client, _ = env
    from app.services.stock import provider

    async def boom(symbols, timeout=6.0):
        return {}

    provider.fetch_quotes = boom
    r = client.get("/api/stock/quotes", params={"symbols": "600519"}, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["source_status"] in ("degraded", "partial", "ok")


# ---------------------------------------------------------------- 触达链路
def test_tick_fires_once_with_cooldown(env, monkeypatch):
    """tick_once 注入恒真时段：拉源→快照→触达→推送打桩；第二轮冷却内不再触发。"""
    client, factory = env
    from app.services.stock import provider, engine
    from app.services.stock.push import push_alert
    from app.workers.stock_worker import tick_once

    async def fake_fetch(symbols, timeout=6.0):
        return {s: _q(price=1700.0) for s in symbols}

    provider.fetch_quotes = fake_fetch

    pushed: list = []

    async def fake_push(db, title, content, receivers):
        pushed.append((title, tuple(receivers)))
        return [{"receiver": r, "msg_id": f"m{len(pushed)}", "status": "dispatched"}
                for r in receivers]

    monkeypatch.setattr("app.services.stock.push.push_alert", fake_push)

    async def setup():
        async with factory() as db:
            from app.models.stock_models import StockAlert
            db.add(StockAlert(symbol="sh600519", name="贵州茅台", condition="price_above",
                              threshold=1690, receiver_tokens=[FRIEND_TK],
                              cooldown_minutes=30, enabled=True))
            await db.commit()

    asyncio.run(setup())

    r1 = asyncio.run(tick_once(now_fn=lambda m: True))
    assert r1["fired"] and r1["quotes"] >= 1
    assert pushed and pushed[0][1] == (FRIEND_TK,)

    # 第二轮：冷却窗口内 → 不再触发
    r2 = asyncio.run(tick_once(now_fn=lambda m: True))
    assert r2["fired"] == [] and len(pushed) == 1

    # 站内触发记录可见（含接收方 msg_id 留痕）
    logs = client.get("/api/stock/alert-logs", headers=ADMIN).json()
    assert logs["total"] == 1
    assert logs["items"][0]["receivers"][0]["msg_id"] == "m1"
    assert logs["items"][0]["pushed"] is True


# ---------------------------------------------------------------- 预警消息体（11-20 改版）
def test_alert_message_body_structure():
    """微信消息体分行含：当前价/时间/涨跌幅/涨跌/昨收/触发条件/投资建议；pushplus 走 text 模板。"""
    from datetime import datetime, timezone

    from app.models.stock_models import StockAlert
    from app.services.stock import engine

    a = StockAlert(symbol="sh600519", condition="pct_down", threshold=3)
    q = {
        "name": "贵州茅台", "price": 1420.0, "prev_close": 1464.0, "open": 1458.0,
        "high": 1461.5, "low": 1418.3, "pct": -3.01, "change": -44.0,
    }
    body = engine.fmt_body(a, q)
    assert "当前价格：1420.00 元" in body
    assert "（-44.00 元（-3.01%）" in body or "-44.00 元（-3.01%）" in body  # 涨跌额+跌幅
    assert "触发盯盘预警" in body
    assert "昨收 1464" in body and "今开 1458" in body
    assert "触发条件：跌幅达到 3%" in body
    assert "投资建议：" in body and "不构成投资建议" in body
    assert "时间：" in body
    assert "\n" in body  # 分行结构

    # 每种条件都有对应建议
    for cond in ("price_above", "price_below", "pct_up", "pct_down"):
        a2 = StockAlert(symbol="sz000001", condition=cond, threshold=2)
        assert "投资建议：" in engine.fmt_body(a2, q), cond

    # pushplus 渠道必须 json 模板（text 无处理器报 600；默认 html 吞换行）
    import asyncio
    from unittest.mock import patch

    from app.services.push_hub import channels as ch

    captured = {}

    class FakeResp:
        status_code = 200
        content = b'{"code":200}'

        def json(self):
            return {"code": 200}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["payload"] = json
            return FakeResp()

    with patch.object(ch.httpx, "AsyncClient", FakeClient):
        asyncio.run(ch.PushPlusProvider("tok").send("SELF", "t", "多行\n正文"))
    assert captured["payload"]["template"] == "json"
    assert captured["payload"]["content"] == "多行\n正文"


# ---------------------------------------------------------------- 推送触达窗口（11-20 需求）
def test_push_window_default_aligned_trading_hours():
    """默认窗口对齐 A 股交易时段 09:15-15:05（收盘后不再挂推送窗口）。"""
    from app.core.config import settings

    assert settings.stock_push_hours == "09:15-15:05"
    assert settings.stock_push_weekdays_only is True


def test_push_window_weekday_hours(monkeypatch):
    """周一至周五 09:15-15:05（北京时间）窗口内可推；周末/时段外拒推。"""
    from datetime import datetime, timezone, timedelta

    from app.core.config import settings
    from app.services.stock import push as push_svc

    # 显式固定窗口值，不依赖默认（默认变更时测试意图仍清晰）
    monkeypatch.setattr(settings, "stock_push_hours", "09:15-15:05")

    CN = timezone(timedelta(hours=8))

    def at(cn_iso: str) -> datetime:
        return datetime.fromisoformat(cn_iso).replace(tzinfo=CN).astimezone(timezone.utc)

    assert push_svc.push_window_open(at("2026-11-20T09:15:00")) is True   # 周五 09:15 边界=开盘
    assert push_svc.push_window_open(at("2026-11-20T15:05:00")) is True   # 周五 15:05 边界=收盘缓冲
    assert push_svc.push_window_open(at("2026-11-20T12:30:00")) is True   # 工作日盘中
    assert push_svc.push_window_open(at("2026-11-20T09:14:00")) is False  # 集合竞价前
    assert push_svc.push_window_open(at("2026-11-20T15:06:00")) is False  # 收盘缓冲后
    assert push_svc.push_window_open(at("2026-11-20T18:00:00")) is False  # 晚间不推（新口径）
    assert push_svc.push_window_open(at("2026-11-21T12:00:00")) is False  # 周六
    assert push_svc.push_window_open(at("2026-11-22T12:00:00")) is False  # 周日
    assert push_svc.push_window_open(at("2026-11-23T12:00:00")) is True   # 周一盘中


def test_push_window_config_parse_and_force_skips_gate():
    """非法时段配置=不限窗口（宁推不误杀）；push_alert force 绕过窗口。"""
    import asyncio
    from datetime import datetime, timezone, timedelta
    from types import SimpleNamespace

    from app.core.config import settings
    from app.services.stock import push as push_svc

    old = settings.stock_push_hours
    try:
        # 非法/空时段=不限小时窗口（但 weekday 闸门仍生效——周六照拒）
        fri_night = datetime(2026, 11, 20, 23, 30, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        sat = datetime(2026, 11, 21, 12, 0, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        settings.stock_push_hours = "bad-spec"
        assert push_svc.push_window_open(fri_night) is True   # 时段不限
        assert push_svc.push_window_open(sat) is False        # 周末仍挡

        settings.stock_push_hours = ""
        assert push_svc.push_window_open(fri_night) is True
    finally:
        settings.stock_push_hours = old

    # 非窗口 → skipped；force 才进 emit。窗口判定本身上面已测，这里桩掉
    # 时钟依赖（防止恰好在窗口边界分钟跑测抖动）。
    called = {}

    async def fake_emit(db, **kw):
        called["yes"] = True
        return "mid-1"

    import app.services.push_hub.emit as emit_svc
    orig_emit = emit_svc.emit
    orig_window = push_svc.push_window_open
    emit_svc.emit = fake_emit
    push_svc.push_window_open = lambda now=None: False
    try:
        db = SimpleNamespace()  # skipped 路径不碰 db
        results = asyncio.run(push_svc.push_alert(db, "t", "b", ["SELF"]))
        assert results[0]["status"] == "skipped:outside_push_window"
        assert "yes" not in called
    finally:
        emit_svc.emit = orig_emit
        push_svc.push_window_open = orig_window
