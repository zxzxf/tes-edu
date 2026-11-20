"""通用 App 推送中心 P1 内核回归（docx/通用App推送中心实施计划.md v1.4 验收锁）。

sqlite 内存真库 + TestClient 全链路（惯例同 test_course_kp_api）：
- 生命周期：注册→pending 门禁 428→admin 审批→登录→建应用→token 档位签发→send→学生收件箱
- token：过期 401 TOKEN_EXPIRED / 续期换发宽限并行 / 吊销 / 停号联动
- 身份域隔离：portal 会话令牌 ≠ pth- 推送 token ≠ 主站注入头，三路互不互认
- 数据墙：A 账号操作 B 应用 → 404（不暴露存在性）
- 幂等：同 msg_id 重复 emit → duplicate；重复 deliver 终态出口吞掉
- Redis 缺席（本环境无 _client）：频控放行、未读回源 PG——降级路径即被测路径
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
    """构造 sqlite 库 + 覆盖 settings 密钥 + 返回 (client, db 工厂)。"""
    settings.push_portal_secret = "test-portal-secret-not-for-prod"
    settings.push_poll_max_wait = 2  # poll 空手超时路径 2s 即返回（默认 25 会拖慢测试）
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    from app.models.course_kb import Base
    import app.models.push_hub  # noqa: F401 注册九表

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
STUDENT = {"X-User-Id": "stu-1001", "X-User-Role": "student", "X-Tenant-Id": "school_pilot_01"}


def _register_login(client, email="dev@corp.com", pwd="Passw0rd!234"):
    r = client.post("/api/push/portal/register",
                    json={"email": email, "password": pwd, "org_name": "示例科技"})
    assert r.status_code == 200, r.text
    acc_id = r.json()["account_id"]
    # pending 门禁：登录拿令牌直接 428
    r = client.post("/api/push/portal/login", json={"email": email, "password": pwd})
    assert r.status_code == 428, r.text
    # admin 审批
    r = client.post(f"/api/admin/push/accounts/{acc_id}/approve",
                    json={"action": "approve"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    r = client.post("/api/push/portal/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, r.text
    return acc_id, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_app_with_token(client, auth, tier=1):
    r = client.post("/api/push/portal/apps",
                    json={"name": "示例推送应用", "allowed_channels": ["app_offline"],
                          "rate_limit_per_min": 100}, headers=auth)
    assert r.status_code == 200, r.text
    app_id = r.json()["app_id"]
    r = client.post(f"/api/push/portal/apps/{app_id}/topics",
                    json={"topic_code": "grade", "name": "成绩", "default_channels": ["app_offline"]},
                    headers=auth)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/push/portal/apps/{app_id}/tokens",
                    json={"tier": tier}, headers=auth)
    assert r.status_code == 200, r.text
    return app_id, r.json()["token"]


def _make_platform_app_with_token(client, tier=1):
    """收件箱只收**平台自有应用**（设计隔离）：admin 建应用+主题+token 走同一条链路。"""
    r = client.post("/api/admin/push/apps", headers=ADMIN,
                    json={"name": "平台学生应用", "allowed_channels": ["app_offline"]})
    assert r.status_code == 200, r.text
    app_id = r.json()["app_id"]
    r = client.post(f"/api/admin/push/apps/{app_id}/topics", headers=ADMIN,
                    json={"topic_code": "grade", "name": "成绩", "default_channels": ["app_offline"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/admin/push/apps/{app_id}/tokens", headers=ADMIN, json={"tier": tier})
    assert r.status_code == 200, r.text
    return app_id, r.json()["token"]


# ---------------------------------------------------------------- 主链路
def test_full_lifecycle_send_to_inbox(env):
    client, _ = env
    _, auth = _register_login(client)  # 第三方链路本身要通（注册/审批/登录/token 签发）
    _, thirdparty_token = _make_app_with_token(client, auth)
    assert client.get("/api/push/open/token-info",
                      headers={"Authorization": f"Bearer {thirdparty_token}"}).status_code == 200

    # 学生收件箱消息源=平台自有应用（第三方误投同一 ID 也不进箱，见下条测试）
    _, token = _make_platform_app_with_token(client)

    r = client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                    json={"topic_code": "grade", "receiver": "stu-1001",
                          "title": "你的作业已批改", "body": "点击查看成绩详情"})
    assert r.status_code == 200, r.text
    mid = r.json()["msg_id"]
    assert r.json()["status"] == "accepted"

    # 学生收件箱（无 Redis → 未读回源 PG 计数）
    r = client.get("/api/student/push/messages", headers=STUDENT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1 and data["unread_total"] == 1
    assert data["unread_by_topic"] == {"grade": 1}
    item = data["items"][0]
    assert item["msg_id"] == mid and item["read"] is False

    # 已读
    r = client.post(f"/api/student/push/messages/{mid}/read", headers=STUDENT)
    assert r.status_code == 200
    r = client.get("/api/student/push/messages", headers=STUDENT)
    assert r.json()["unread_total"] == 0 and r.json()["items"][0]["read"] is True


def test_send_ignores_other_receiver_param(env):
    """身份铁律：X-User-Id 是 stu-1002，传别人的收件人也只看自己的。"""
    client, _ = env
    _, auth = _register_login(client)
    _, token = _make_app_with_token(client, auth)
    client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                json={"topic_code": "grade", "receiver": "stu-1001", "title": "T", "body": "B"})
    r = client.get("/api/student/push/messages",
                   headers={"X-User-Id": "stu-1002", "X-User-Role": "student", "X-Tenant-Id": "t2"})
    assert r.json()["total"] == 0


# ---------------------------------------------------------------- token 生命周期
def test_token_expiry_401(env):
    client, factory = env
    _, auth = _register_login(client)
    app_id, token = _make_app_with_token(client, auth)
    from app.models.push_hub import PushToken
    from sqlalchemy import select

    async def expire():
        async with factory() as s:
            t = (await s.execute(select(PushToken).where(PushToken.app_id == app_id))).scalars().first()
            t.expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await s.commit()

    asyncio.run(expire())
    r = client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                    json={"topic_code": "grade", "receiver": "stu-1001", "title": "T", "body": "B"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "TOKEN_EXPIRED"
    assert "expired_at" in r.json()["detail"]


def test_token_rotate_grace_and_revoke(env):
    client, factory = env
    _, auth = _register_login(client)
    app_id, token_old = _make_app_with_token(client, auth)
    # 再签发=换发：旧 token 进 7 天宽限，仍可用
    r = client.post(f"/api/push/portal/apps/{app_id}/tokens", json={"tier": 3}, headers=auth)
    assert r.status_code == 200
    token_new = r.json()["token"]
    for tk in (token_old, token_new):
        r = client.get("/api/push/open/token-info", headers={"Authorization": f"Bearer {tk}"})
        assert r.status_code == 200, tk

    # 吊销新 token → 401；旧 token（rotated 但宽限内）仍可用
    info = client.get(f"/api/push/portal/apps/{app_id}/tokens", headers=auth).json()
    new_id = [t for t in info if t["token_prefix"] == token_new[:8]][0]["token_id"]
    assert client.post(f"/api/push/portal/tokens/{new_id}/revoke", headers=auth).status_code == 200
    assert client.get("/api/push/open/token-info",
                      headers={"Authorization": f"Bearer {token_new}"}).status_code == 401

    # 模拟宽限过期：rotated 行 grace_until 提前 → 旧 token 401
    from app.models.push_hub import PushToken
    from sqlalchemy import select

    async def over():
        async with factory() as s:
            t = (await s.execute(select(PushToken).where(PushToken.app_id == app_id))).scalars().all()
            for x in t:
                if x.status == "rotated":
                    x.grace_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            await s.commit()

    asyncio.run(over())
    assert client.get("/api/push/open/token-info",
                      headers={"Authorization": f"Bearer {token_old}"}).status_code == 401


def test_account_suspend_kills_tokens(env):
    client, factory = env
    acc_id, auth = _register_login(client)
    _, token = _make_app_with_token(client, auth)
    assert client.get("/api/push/open/token-info",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200
    r = client.post(f"/api/admin/push/accounts/{acc_id}/approve",
                    json={"action": "suspend"}, headers=ADMIN)
    assert r.status_code == 200
    r = client.get("/api/push/open/token-info", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
    # portal 会话同样被停
    assert client.get("/api/push/portal/me", headers=auth).status_code == 403


# ---------------------------------------------------------------- 身份域隔离
def test_three_auth_realms_do_not_interop(env):
    client, _ = env
    _, auth = _register_login(client)  # portal 会话令牌
    _, token = _make_app_with_token(client, auth)  # pth- 推送 token

    # 1) portal 令牌调数据面：pth 格式校验先拒 → 401
    r = client.get("/api/push/open/token-info", headers=auth)
    assert r.status_code == 401
    # 2) pth 令牌调 portal：非 JWT → 401
    r = client.get("/api/push/portal/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    # 3) portal 令牌调 admin 面：admin 只认网关注入头，Bearer 无关 → 401 缺头
    r = client.get("/api/admin/push/stats", headers=auth)
    assert r.status_code == 401
    # 4) 主站 admin 注入头调 portal：无 portal 令牌 → 401
    r = client.get("/api/push/portal/me", headers=ADMIN)
    assert r.status_code == 401
    # 5) 学生身份头调数据面：无 pth → 401
    r = client.get("/api/push/open/topics", headers=STUDENT)
    assert r.status_code == 401


# ---------------------------------------------------------------- 数据墙/越权
def test_cross_account_isolation_404(env):
    client, _ = env
    _, authA = _register_login(client, email="a@corp.com")
    _, authB = _register_login(client, email="b@corp.com")
    appA, _ = _make_app_with_token(client, authA)
    # B 读 A 的应用消息/统计/token → 404 不暴露存在性
    for url in (f"/api/push/portal/apps/{appA}/messages",
                f"/api/push/portal/apps/{appA}/stats",
                f"/api/push/portal/apps/{appA}/tokens",
                f"/api/push/portal/apps/{appA}/topics"):
        r = client.get(url, headers=authB)
        assert r.status_code == 404, (url, r.text)
    # B 在 A 的应用下建主题 → 404
    r = client.post(f"/api/push/portal/apps/{appA}/topics",
                    json={"topic_code": "hack", "name": "X"}, headers=authB)
    assert r.status_code == 404


def test_role_gate_viewer_cannot_write(env):
    client, factory = env
    acc_id, auth = _register_login(client)
    from app.models.push_hub import PushAccountRole
    from sqlalchemy import delete

    async def to_viewer():
        async with factory() as s:
            await s.execute(delete(PushAccountRole).where(PushAccountRole.account_id == acc_id))
            s.add(PushAccountRole(account_id=acc_id, role="viewer"))
            await s.commit()

    asyncio.run(to_viewer())
    # viewer 登录令牌里 roles 是旧快照也会被 DB 回查覆盖 → 写应用被 403
    r = client.post("/api/push/portal/apps", json={"name": "n"}, headers=auth)
    assert r.status_code == 403


# ---------------------------------------------------------------- 主题/幂等/频控
def test_topic_not_registered_422_and_duplicate_idempotent(env):
    client, _ = env
    _, auth = _register_login(client)
    _, token = _make_app_with_token(client, auth)
    hdr = {"Authorization": f"Bearer {token}"}

    r = client.post("/api/push/open/send", headers=hdr,
                    json={"topic_code": "nope", "receiver": "stu-1001", "title": "T", "body": "B"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "TOPIC_NOT_REGISTERED"

    # 同 msg_id 重发=幂等 duplicate（内部 emit 路径直接验，OpenAPI 每次自生成 uuid）
    from app.services.push_hub import emit as emit_svc
    from app.core.db import SessionLocal
    import uuid

    mid = str(uuid.uuid4())

    async def dup():
        async with SessionLocal() as s:
            from app.models.push_hub import PushApp
            from sqlalchemy import select
            app = (await s.execute(select(PushApp).where(PushApp.app_id == 1))).scalar_one()
            first = await emit_svc.emit(s, app_id=app.app_id, topic_code="grade",
                                        receiver="stu-9", title="T", body="B", msg_id=mid)
            with pytest.raises(emit_svc.DuplicateMsg):
                await emit_svc.emit(s, app_id=app.app_id, topic_code="grade",
                                    receiver="stu-9", title="T", body="B", msg_id=mid)
            return first

    assert asyncio.run(dup()) == mid


def test_app_disable_blocks_send_403(env):
    client, _ = env
    _, auth = _register_login(client)
    app_id, token = _make_app_with_token(client, auth)
    r = client.delete(f"/api/push/portal/apps/{app_id}", headers=auth)
    assert r.status_code == 200
    r = client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                    json={"topic_code": "grade", "receiver": "stu-1001", "title": "T", "body": "B"})
    # verify 层联动：应用禁用 → INVALID
    assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"


def test_poll_returns_fast_with_new_message(env):
    """有消息时 poll 首查即返（断言远小于 CF 30s 红线）；空手按 max_wait 返回。"""
    import time

    client, _ = env
    _, token = _make_platform_app_with_token(client)
    client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                json={"topic_code": "grade", "receiver": "stu-1001", "title": "T", "body": "B"})

    t0 = time.monotonic()
    r = client.get("/api/student/push/messages/poll", params={"since_us": 0}, headers=STUDENT)
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    assert time.monotonic() - t0 < 5  # 首查即得

    t0 = time.monotonic()
    r = client.get("/api/student/push/messages/poll",
                   params={"since_us": r.json()["max_created_us"]}, headers=STUDENT)
    assert r.json()["items"] == []
    assert time.monotonic() - t0 < 30, "poll 挂起必须短于 Cloudflare 30s 边缘超时"


# ---------------------------------------------------------------- 渠道配置加密
def test_channel_credentials_encrypted_and_never_echoed(env):
    from cryptography.fernet import Fernet

    settings.push_secret_key = Fernet.generate_key().decode()
    client, _ = env
    r = client.put("/api/admin/push/channels/wechat_oa", headers=ADMIN,
                   json={"channel_type": "wechat_oa", "name": "微信公众号",
                         "enabled": True, "rate_limit_per_min": 60,
                         "credentials": {"pushplus_token": "pp-secret-token"}})
    assert r.status_code == 200
    rows = client.get("/api/admin/push/channels", headers=ADMIN).json()
    row = [x for x in rows if x["channel_type"] == "wechat_oa"][0]
    assert row["has_credentials"] is True
    assert "pushplus_token" not in str(rows)  # 列表不回显任何凭证

    # 无密钥时 fail-closed
    settings.push_secret_key = ""
    r = client.put("/api/admin/push/channels/app_offline", headers=ADMIN,
                   json={"channel_type": "app_offline", "name": "App收件箱", "enabled": True,
                         "credentials": {"x": "y"}})
    assert r.status_code == 500 or r.status_code == 400 or r.status_code == 422
    assert "PUSH_SECRET_KEY" in r.text


# ---------------------------------------------------------------- P2 管理页只读接口
def test_admin_readonly_endpoints(env):
    """P2 页面依赖的只读面：apps 列表 / topics / tokens（仅前缀）/ messages / resend。"""
    client, _ = env
    _, auth = _register_login(client)
    app_id, token = _make_app_with_token(client, auth)
    # 发一条消息进账
    r = client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                    json={"topic_code": "grade", "receiver": "stu-9", "title": "T", "body": "B"})
    assert r.status_code == 200, r.text
    msg_id = r.json()["msg_id"]

    apps = client.get("/api/admin/push/apps", headers=ADMIN).json()
    assert any(a["app_id"] == app_id and a["owner_email"] == "dev@corp.com" for a in apps)
    # 越权防护口径：第三方应用 admin 可见（审批职责），平台自有 owner=None
    tops = client.get(f"/api/admin/push/apps/{app_id}/topics", headers=ADMIN).json()
    assert tops and tops[0]["topic_code"] == "grade"
    topic_id = tops[0]["topic_id"]
    toks = client.get(f"/api/admin/push/apps/{app_id}/tokens", headers=ADMIN).json()
    assert toks and toks[0]["token_prefix"].startswith("pth-")
    assert "pth-" + "0" * 32 not in str(toks)  # 明文永不回显（只有前缀）
    msgs = client.get("/api/admin/push/messages", params={"app_id": app_id}, headers=ADMIN).json()
    assert msgs["total"] >= 1
    m = [x for x in msgs["items"] if x["msg_id"] == msg_id][0]
    assert m["status"] in ("pending", "sent", "partial")  # sqlite 无 Kafka：emit 就地分发后终态

    # 主题启停（admin toggle）
    r = client.patch(f"/api/admin/push/topics/{topic_id}", headers=ADMIN, json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    # 停用后 send 被拒
    r = client.post("/api/push/open/send", headers={"Authorization": f"Bearer {token}"},
                    json={"topic_code": "grade", "receiver": "stu-9", "title": "T2", "body": "B2"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "TOPIC_DISABLED"
    r = client.patch(f"/api/admin/push/topics/{topic_id}", headers=ADMIN, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True

    # resend：非 dead/expired 拒绝（sqlite 同步分发后消息已是终态 sent/partial）
    r = client.post(f"/api/admin/push/messages/{msg_id}/resend", headers=ADMIN)
    assert r.status_code in (200, 409)


# ---------------------------------------------------------------- P4 判分/下发业务钩子
def test_business_grade_hook_emits_to_platform_inbox(env):
    """判分完成钩子：grade 主题消息进平台学生收件箱；幂等 msg_id 重复调只一条。"""
    import asyncio

    from app.services.push_hub import business as biz

    client, _ = env
    _make_platform_app_with_token(client)  # 建平台自有应用 + grade 主题

    async def fire():
        await biz._emit_to_platform_app(
            topic_code="grade", receiver="stu-1001", title="成绩已出",
            body="作业已批阅：8/10 分", msg_id="grade-sub-777",
            payload={"kind": "grade", "submission_id": 777},
        )
        # 幂等：同 msg_id 再来一次，落库层去重，不抛异常
        await biz._emit_to_platform_app(
            topic_code="grade", receiver="stu-1001", title="成绩已出",
            body="作业已批阅：8/10 分", msg_id="grade-sub-777",
        )

    asyncio.run(fire())
    data = client.get("/api/student/push/messages", headers=STUDENT).json()
    grade_msgs = [m for m in data["items"] if m["topic_code"] == "grade"]
    assert len(grade_msgs) == 1, "幂等 msg_id 必须只落一条"
    assert grade_msgs[0]["payload"]["submission_id"] == 777


def test_business_hook_skips_missing_topic(env):
    """主题未建/未启用 → 静默跳过，不抛异常、不落消息（老环境兼容）。"""
    import asyncio

    from app.services.push_hub import business as biz

    client, _ = env
    _make_platform_app_with_token(client)  # 只有 grade 主题

    asyncio.run(biz._emit_to_platform_app(
        topic_code="homework", receiver="stu-1001", title="新作业",
        body="B", msg_id="homework-asg-1-stu-1001",
    ))
    assert client.get("/api/student/push/messages", headers=STUDENT).json()["total"] == 0


def test_business_switch_off_skips_all(env):
    """总开关关闭 → 任何业务钩子都不发（紧急停推送）。"""
    import asyncio

    from app.core.config import settings
    from app.services.push_hub import business as biz

    client, _ = env
    _make_platform_app_with_token(client)
    old = settings.push_business_notify_enabled
    settings.push_business_notify_enabled = False
    try:
        asyncio.run(biz._emit_to_platform_app(
            topic_code="grade", receiver="stu-1001", title="T", body="B", msg_id="off-1"))
        assert client.get("/api/student/push/messages", headers=STUDENT).json()["total"] == 0
    finally:
        settings.push_business_notify_enabled = old


def test_new_message_invalidates_unread_agg_cache(monkeypatch):
    """新消息落 app_offline 必须失效未读聚合键——否则收件箱未读停在旧值（P4 生产实测 bug）。"""
    import asyncio
    from unittest.mock import MagicMock

    from app.services.push_hub import channels as ch
    from app.services.push_hub.channels import unread_agg_key

    fake = MagicMock()
    # 异步方法需 AsyncMock
    from unittest.mock import AsyncMock
    fake.incr = AsyncMock()
    fake.zadd = AsyncMock()
    fake.zremrangebyrank = AsyncMock()
    fake.publish = AsyncMock()
    fake.delete = AsyncMock()
    monkeypatch.setattr(ch.cache, "_get_client", lambda: fake)

    asyncio.run(ch.bump_inbox_counters(1, "stu-agg-test", "grade", "m-agg-1"))
    fake.delete.assert_awaited_once_with(unread_agg_key("stu-agg-test"))


# ---------------------------------------------------------------- P4 主题静音开关
def test_subscription_mute_toggle_and_isolation(env):
    """App 消息设置：静音开关读写 + 他人隔离 + 未订阅主题拒改。"""
    client, _ = env
    _make_platform_app_with_token(client)  # 平台应用 + grade 主题

    # 初始：grade 主题列出、未静音
    st, data = client.get("/api/student/push/subscriptions", headers=STUDENT).status_code, \
        client.get("/api/student/push/subscriptions", headers=STUDENT).json()
    assert st == 200 and any(x["topic_code"] == "grade" and not x["muted"] for x in data["items"])

    # 开静音（重复两次验 upsert 幂等）
    for _ in range(2):
        r = client.put("/api/student/push/subscriptions/grade", params={"muted": True}, headers=STUDENT)
        assert r.status_code == 200 and r.json()["muted"] is True
    data = client.get("/api/student/push/subscriptions", headers=STUDENT).json()
    assert [x for x in data["items"] if x["topic_code"] == "grade"][0]["muted"] is True

    # 他人身份：不受我的静音影响
    other = {"X-User-Id": "stu-mute-other", "X-User-Role": "student", "X-Tenant-Id": "school_pilot_01"}
    data2 = client.get("/api/student/push/subscriptions", headers=other).json()
    assert [x for x in data2["items"] if x["topic_code"] == "grade"][0]["muted"] is False

    # 未建的主题拒改（防越权配第三方/不存在主题）
    r = client.put("/api/student/push/subscriptions/homework", params={"muted": True}, headers=STUDENT)
    assert r.status_code == 404

    # 关静音恢复
    r = client.put("/api/student/push/subscriptions/grade", params={"muted": False}, headers=STUDENT)
    assert r.status_code == 200 and r.json()["muted"] is False


# ---------------------------------------------------------------- SELF 语义（大小写归一）
def test_pushplus_self_receiver_is_case_insensitive(monkeypatch):
    """推自己占位 SELF 大小写不敏感——盯盘页传小写 self 曾被当好友令牌走 to 转发（11-20 实测 dead）。"""
    import asyncio

    from app.services.push_hub import channels as ch

    captured = {}

    class FakeResp:
        status_code = 200
        content = b'{"code":200,"msg":"ok"}'

        def json(self):
            return {"code": 200, "msg": "ok"}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):  # noqa: A002 对齐 httpx 签名
            captured["payload"] = json
            return FakeResp()

    monkeypatch.setattr(ch.httpx, "AsyncClient", FakeClient)

    p = ch.PushPlusProvider("tok-channel-32")
    for r in ("SELF", "self", " Self ", "tok-channel-32", ""):
        captured.clear()
        code, _ = asyncio.run(p.send(r, "t", "b"))
        assert code == "ok"
        assert "to" not in captured["payload"], f"receiver={r!r} 必须走渠道主一对一，不得 to 转发"
    # 好友令牌仍走 to 转发
    captured.clear()
    asyncio.run(p.send("tok-friend-xx", "t", "b"))
    assert captured["payload"]["to"] == "tok-friend-xx"
