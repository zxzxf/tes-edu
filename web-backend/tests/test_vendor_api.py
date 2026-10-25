"""T-AM6 厂商与数据服务实例集成单测（sqlite 内存库，不连生产库）。

验证：
- 厂商 CRUD + 凭证「只写不回显」：响应无 credential 字段，DB 落密文
- 凭证更新语义：不传保持、传非空覆盖重加密
- 实例 CRUD + vendor_id 路径强制隔离（跨厂商 404）
- 删除守卫：厂商下有实例 400
- 连通性测试：mock httpx，200/网络错误/缺凭证缺 base_url 各分支
- 权限：读仅 admin；写走 system:vendor:manage（parent 403）
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import jwt_utils
from app.models import rbac_models  # noqa: F401 注册权限表（含 vendor/instance 两张表）
from app.models.course_kb import Base
from main import app

# 固定测试密钥（hex 32 字节），避免依赖 JWT_PRIVATE_KEY 派生路径
os.environ["VENDOR_CREDENTIAL_KEY"] = "ab" * 32

ADMIN = {"X-User-Id": "u_admin", "X-User-Role": "admin"}
PARENT = {"X-User-Id": "u_parent", "X-User-Role": "parent"}


async def _async_noop(*args, **kwargs):
    return None


@pytest.fixture()
def client():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    os.environ["JWT_PRIVATE_KEY"] = pem
    jwt_utils.JWT_PRIVATE_KEY = pem

    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as s:
            yield s

    from app.core.db import get_db

    app.dependency_overrides[get_db] = override_get_db

    async def bootstrap():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as db:
            from passlib.context import CryptContext

            pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
            from app.services import rbac_seed

            await rbac_seed.seed_rbac(db, pwd.hash("demo1234"))

    asyncio.run(bootstrap())

    import app.core.db as db_mod
    import app.services.course_kb as course_kb_mod
    import app.services.rbac_seed as rbac_seed_mod

    db_mod.engine = engine
    db_mod.SessionLocal = session_factory
    course_kb_mod.init_course_kb = _async_noop
    rbac_seed_mod.init_rbac = _async_noop

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def _login(client, username, password="demo1234"):
    r = client.post("/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


VENDOR_BODY = {
    "vendor_id": "v_aliyun",
    "vendor_name": "阿里云百炼",
    "vendor_type": "aliyun",
    "base_url": "https://dashscope.aliyuncs.com",
    "auth_type": "apikey",
    "credential": "sk-topsecret-123",
}


def _create_vendor(client, body=None):
    return client.post("/api/admin/vendor", json=body or VENDOR_BODY, headers=ADMIN)


INSTANCE_BODY = {
    "instance_id": "inst_aliyun_main",
    "service_name": "主数据服务",
    "data_namespace": "ns_aliyun_main",
    "isolation_mode": "logical",
    "connection": "postgres://u:p@h:5432/db",
}


def test_vendor_crud_credential_never_echoed(client):
    """凭证只写不回显：响应无 credential/credential_enc，DB 落的是密文。"""
    _login(client, "admin@tes.edu")

    r = _create_vendor(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_credential"] is True
    assert body["instance_count"] == 0
    # 响应里任何位置都不出现明文凭证
    assert "sk-topsecret-123" not in r.text
    assert "credential" not in body  # 连字段名都不给（has_credential 除外）

    # 加密落库证明：直查原始列，明文不在、长度超过 nonce+tag
    import app.core.db as db_mod

    async def _raw():
        async with db_mod.engine.begin() as conn:
            row = (await conn.execute(
                text("SELECT credential_enc FROM sys_service_vendor WHERE vendor_id='v_aliyun'")
            )).first()
        return row[0] if row else None

    blob = asyncio.run(_raw())
    assert blob is not None
    assert b"sk-topsecret-123" not in blob
    assert len(blob) > 28  # nonce(12) + tag(16) 至少

    # 列表/单条同样不回显
    rows = client.get("/api/admin/vendor", headers=ADMIN).json()
    assert "sk-topsecret-123" not in str(rows)
    one = client.get("/api/admin/vendor/v_aliyun", headers=ADMIN).json()
    assert one["has_credential"] is True and "credential" not in one

    # 更新不传凭证 → 保持已存（has_credential 仍为 true）
    r2 = client.put(
        "/api/admin/vendor/v_aliyun",
        json={**VENDOR_BODY, "credential": None, "vendor_name": "阿里云百炼(改)"},
        headers=ADMIN,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["vendor_name"] == "阿里云百炼(改)"
    assert r2.json()["has_credential"] is True

    # 更新传新凭证 → 覆盖重加密
    r3 = client.put(
        "/api/admin/vendor/v_aliyun",
        json={**VENDOR_BODY, "credential": "sk-new-456"},
        headers=ADMIN,
    )
    assert r3.status_code == 200 and r3.json()["has_credential"] is True

    # 删除：无实例 → 204；再删 404
    r4 = client.delete("/api/admin/vendor/v_aliyun", headers=ADMIN)
    assert r4.status_code == 204
    assert client.delete("/api/admin/vendor/v_aliyun", headers=ADMIN).status_code == 404


def test_vendor_delete_guard_blocks_when_instances_exist(client):
    """删除守卫：厂商下仍有实例时 400，清空实例后才可删。"""
    _login(client, "admin@tes.edu")
    assert _create_vendor(client).status_code == 201
    assert client.post(
        "/api/admin/vendor/v_aliyun/instances", json=INSTANCE_BODY, headers=ADMIN
    ).status_code == 201

    r = client.delete("/api/admin/vendor/v_aliyun", headers=ADMIN)
    assert r.status_code == 400, r.text
    assert "1 instances" in r.json()["detail"]

    # 删实例后厂商可删
    assert client.delete(
        "/api/admin/vendor/v_aliyun/instances/inst_aliyun_main", headers=ADMIN
    ).status_code == 204
    assert client.delete("/api/admin/vendor/v_aliyun", headers=ADMIN).status_code == 204


def test_instance_crud_and_vendor_path_isolation(client):
    """实例 CRUD + vendor_id 路径强制隔离：跨厂商读写一律 404。"""
    _login(client, "admin@tes.edu")
    assert _create_vendor(client).status_code == 201
    assert _create_vendor(client, {
        **VENDOR_BODY, "vendor_id": "v_tencent", "vendor_name": "腾讯云",
        "vendor_type": "tencent", "credential": None,
    }).status_code == 201

    r = client.post(
        "/api/admin/vendor/v_aliyun/instances", json=INSTANCE_BODY, headers=ADMIN
    )
    assert r.status_code == 201, r.text
    assert r.json()["has_connection"] is True
    assert "postgres://" not in r.text  # 连接串同样不回显

    # 列表按 vendor 归属
    rows = client.get("/api/admin/vendor/v_aliyun/instances", headers=ADMIN).json()
    assert [x["instance_id"] for x in rows] == ["inst_aliyun_main"]
    assert client.get("/api/admin/vendor/v_tencent/instances", headers=ADMIN).json() == []

    # 跨厂商路径隔离：用 v_tencent 的路径读写 v_aliyun 的实例 → 404
    r2 = client.put(
        "/api/admin/vendor/v_tencent/instances/inst_aliyun_main",
        json={**INSTANCE_BODY, "service_name": "越权改"},
        headers=ADMIN,
    )
    assert r2.status_code == 404, r2.text
    r3 = client.delete(
        "/api/admin/vendor/v_tencent/instances/inst_aliyun_main", headers=ADMIN
    )
    assert r3.status_code == 404, r3.text

    # 正确路径下更新成功；不传 connection 保持已存
    r4 = client.put(
        "/api/admin/vendor/v_aliyun/instances/inst_aliyun_main",
        json={**INSTANCE_BODY, "connection": None, "service_name": "主数据服务(改)"},
        headers=ADMIN,
    )
    assert r4.status_code == 200, r4.text
    assert r4.json()["service_name"] == "主数据服务(改)"
    assert r4.json()["has_connection"] is True

    # 重复 instance_id → 409
    r5 = client.post(
        "/api/admin/vendor/v_aliyun/instances", json=INSTANCE_BODY, headers=ADMIN
    )
    assert r5.status_code == 409, r5.text


def test_vendor_permissions_read_admin_write_manage(client):
    """读仅 admin；写走 system:vendor:manage 按钮码（parent 403）。"""
    _login(client, "admin@tes.edu")
    _login(client, "parent@tes.edu")

    assert client.get("/api/admin/vendor", headers=PARENT).status_code == 403
    r = client.post("/api/admin/vendor", json=VENDOR_BODY, headers=PARENT)
    assert r.status_code == 403, r.text

    # admin 建好后 parent 仍写不动
    assert _create_vendor(client).status_code == 201
    assert client.put(
        "/api/admin/vendor/v_aliyun", json=VENDOR_BODY, headers=PARENT
    ).status_code == 403
    assert client.delete("/api/admin/vendor/v_aliyun", headers=PARENT).status_code == 403


class _FakeResp:
    status_code = 200


def test_vendor_connectivity_test(client):
    """连通性测试：mock httpx，覆盖 200 / 网络错误 / 缺凭证 / 缺 base_url。"""
    _login(client, "admin@tes.edu")
    assert _create_vendor(client).status_code == 201

    mock_get = AsyncMock(return_value=_FakeResp())
    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        r = client.post("/api/admin/vendor/v_aliyun/test", headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["status_code"] == 200
    assert "sk-topsecret-123" not in r.text  # 测试过程也不吐凭证
    # 断言实调时带了 Bearer 头（凭证解密后用于调用）
    args, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer sk-topsecret-123"

    # 网络错误 → ok=false，不 500
    with patch.object(
        httpx.AsyncClient, "get",
        new=AsyncMock(side_effect=httpx.ConnectError("connection refused")),
    ):
        r2 = client.post("/api/admin/vendor/v_aliyun/test", headers=ADMIN)
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is False
    assert "ConnectError" in r2.json()["detail"]

    # 缺 base_url / 缺凭证 → 400
    assert client.post(
        "/api/admin/vendor", json={**VENDOR_BODY, "vendor_id": "v_nourl", "base_url": None},
        headers=ADMIN,
    ).status_code == 201
    r3 = client.post("/api/admin/vendor/v_nourl/test", headers=ADMIN)
    assert r3.status_code == 400 and "base_url" in r3.json()["detail"]

    assert client.post(
        "/api/admin/vendor",
        json={**VENDOR_BODY, "vendor_id": "v_nocred", "credential": None},
        headers=ADMIN,
    ).status_code == 201
    r4 = client.post("/api/admin/vendor/v_nocred/test", headers=ADMIN)
    assert r4.status_code == 400 and "credential" in r4.json()["detail"]

    # 厂商不存在 → 404
    assert client.post("/api/admin/vendor/v_none/test", headers=ADMIN).status_code == 404
