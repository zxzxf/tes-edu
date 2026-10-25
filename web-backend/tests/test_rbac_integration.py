"""T-AM0/1/2/3/4/5 权限基座集成单测（sqlite 内存库，不连生产库）。

验证主干"菜单+角色+按钮级"：
- 启动建表 + 种子（7区域/5角色/菜单树/管理员）
- admin 登录后 /menu/my 返回全量菜单（含按钮码）
- parent 登录后 /menu/my 仅返回 家长周报（m_ops_reports）
- 菜单 CRUD：admin 可建/改/删；非 admin 403
- 区域 CRUD：admin 全量 / region_admin 仅辖区；删除守卫挡绑定租户
- 角色授权：admin 可重设角色菜单
- 越权：parent 调 /menu（管理接口）应 403
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import jwt_utils
from app.models import rbac_models  # noqa: F401 注册权限表
from app.models.course_kb import Base
from main import app


async def _async_noop(*args, **kwargs):
    return None


@pytest.fixture()
def client():
    # 临时 RSA 私钥 + sqlite 内存库
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    os.environ["JWT_PRIVATE_KEY"] = pem
    jwt_utils.JWT_PRIVATE_KEY = pem

    # 共享同一连接，保证内存库跨 session 可见
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

            stats = await rbac_seed.seed_rbac(db, pwd.hash("demo1234"))
            assert stats["roles"] == 5 and stats["menus"] == len(rbac_seed.MENUS)
            # 关键断言：种子必须建立用户-角色关联，否则 /login 查不到 role_code
            # （T-AM7：五角色演示账号各一条，含租户级 school/teacher/parent）
            assert stats["user_roles"] == 5, f"种子漏建用户-角色关联: {stats}"
            assert stats["users"] == 5, f"种子漏建演示账号: {stats}"

    asyncio.run(bootstrap())

    # 让 lifespan 复用测试引擎、且不再跑 pgvector 扩展（sqlite 不支持），
    # 避免 module 引擎泄漏导致进程退出挂起、以及 CREATE EXTENSION 语法错误。
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


def test_admin_menu_my_returns_full_tree(client):
    token = _login(client, "admin@tes.edu")
    r = client.get("/api/admin/menu/my", headers={"X-User-Id": "u_admin", "X-User-Role": "admin"})
    assert r.status_code == 200, r.text
    names = {m["menu_name"] for m in r.json()}
    assert "系统设置" in names
    assert "菜单管理" in names
    assert "家长周报" in names
    # 按钮码也在菜单树里
    codes = {m["permission_code"] for m in r.json() if m["permission_code"]}
    assert "system:menu:add" in codes


def test_parent_menu_my_only_reports(client):
    # T-AM7 起 parent@tes.edu 有种子账号（u_parent + role_parent），
    # /menu/my 按角色聚合：仅「区域运营」DIR + 「家长周报」，看不到运维页面
    token = _login(client, "parent@tes.edu")  # 种子账号，DB bcrypt 校验
    r = client.get("/api/admin/menu/my", headers={"X-User-Id": "u_parent", "X-User-Role": "parent"})
    assert r.status_code == 200
    names = {x["menu_name"] for x in r.json()}
    assert names == {"区域运营", "家长周报"}, names


def test_menu_crud_admin_ok_others_forbidden(client):
    _login(client, "admin@tes.edu")
    body = {
        "menu_id": "m_test", "menu_name": "测试菜单", "menu_type": "MENU",
        "path": "/test", "sort": 99,
    }
    r = client.post(
        "/api/admin/menu", json=body,
        headers={"X-User-Id": "u_admin", "X-User-Role": "admin"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["menu_name"] == "测试菜单"

    # 非 admin 调管理接口应 403
    r2 = client.get(
        "/api/admin/menu",
        headers={"X-User-Id": "u_parent", "X-User-Role": "parent"},
    )
    assert r2.status_code == 403, r2.text

    # 删除
    r3 = client.delete(
        "/api/admin/menu/m_test",
        headers={"X-User-Id": "u_admin", "X-User-Role": "admin"},
    )
    assert r3.status_code == 204


def test_role_authorize_admin_ok(client):
    _login(client, "admin@tes.edu")
    r = client.put(
        "/api/admin/role/role_teacher/menus",
        json={"menu_ids": ["m_ops", "m_ops_reports", "m_ops_analytics"]},
        headers={"X-User-Id": "u_admin", "X-User-Role": "admin"},
    )
    assert r.status_code == 204, r.text


def test_role_menus_read_back_for_auth_tree(client):
    """T-AM4 前端授权树回显：GET /role/{id}/menus 必须能读回当前授权——
    PUT 是全量替换语义，读不回来前端就无法安全提交勾选结果。"""
    headers = {"X-User-Id": "u_admin", "X-User-Role": "admin"}
    _login(client, "admin@tes.edu")

    r = client.put(
        "/api/admin/role/role_teacher/menus",
        json={"menu_ids": ["m_ops", "m_ops_reports", "m_ops_analytics"]},
        headers=headers,
    )
    assert r.status_code == 204, r.text

    r2 = client.get("/api/admin/role/role_teacher/menus", headers=headers)
    assert r2.status_code == 200, r2.text
    assert set(r2.json()["menu_ids"]) == {"m_ops", "m_ops_reports", "m_ops_analytics"}

    # 非 admin 403；不存在角色 404
    r3 = client.get(
        "/api/admin/role/role_teacher/menus",
        headers={"X-User-Id": "u_parent", "X-User-Role": "parent"},
    )
    assert r3.status_code == 403, r3.text
    r4 = client.get("/api/admin/role/role_none/menus", headers=headers)
    assert r4.status_code == 404, r4.text


def test_platform_users_token_carries_tenant(client):
    """T-AM7：平台级用户（admin/region_admin，sys_user.tenant_id 为空）签发的
    JWT 必须带非空 tenant_id（"platform" 占位）——网关 /api/admin/ 路由
    require_tenant 会 403 挡掉无租户 claim 的令牌，动态菜单 /menu/my 随之全灭。
    """
    from cryptography.hazmat.primitives import serialization
    from jose import jwt as jose_jwt

    key = serialization.load_pem_private_key(
        os.environ["JWT_PRIVATE_KEY"].encode(), password=None
    )
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    for username in ("admin@tes.edu", "region_admin@tes.edu"):
        token = _login(client, username)
        claims = jose_jwt.decode(token, pub_pem, algorithms=["RS256"])
        assert claims["tenant_id"] == "platform", (
            f"{username} 令牌缺 tenant_id，将被网关 require_tenant 403 挡死"
        )
        assert claims["role"] in ("admin", "region_admin")


def test_region_list_admin_full_region_admin_scoped(client):
    """T-AM5：admin 见 7 区域全量；region_admin 仅见本辖区（回查 sys_user.region_id）。
    计数口径：种子 4 个演示账号 region_id=rgn_east，故华东 user_count=4。"""
    _login(client, "admin@tes.edu")
    r = client.get("/api/admin/region", headers={"X-User-Id": "u_admin", "X-User-Role": "admin"})
    assert r.status_code == 200, r.text
    rows = r.json()
    codes = {x["region_code"] for x in rows}
    assert codes == {"east", "north", "central", "south", "southwest", "northwest", "northeast"}
    by_code = {x["region_code"]: x for x in rows}
    assert by_code["east"]["user_count"] == 4, by_code["east"]
    assert by_code["north"]["user_count"] == 0
    # 种子未建学校，全部 tenant_count 为 0
    assert all(x["tenant_count"] == 0 for x in rows)

    r2 = client.get(
        "/api/admin/region",
        headers={"X-User-Id": "u_region_admin", "X-User-Role": "region_admin"},
    )
    assert r2.status_code == 200, r2.text
    assert [x["region_code"] for x in r2.json()] == ["east"]

    # 无角色头 401；parent 403
    assert client.get("/api/admin/region").status_code == 401
    r4 = client.get("/api/admin/region", headers={"X-User-Id": "u_parent", "X-User-Role": "parent"})
    assert r4.status_code == 403, r4.text


def test_region_crud_admin_ok_others_forbidden(client):
    """T-AM5：区域 CRUD + 按钮级权限（system:region:add/edit/delete）。"""
    headers = {"X-User-Id": "u_admin", "X-User-Role": "admin"}
    _login(client, "admin@tes.edu")

    # 非 admin 写操作 403（按钮级）
    r_forbidden = client.post(
        "/api/admin/region",
        json={"region_id": "rgn_test", "region_code": "test", "region_name": "测试区域"},
        headers={"X-User-Id": "u_parent", "X-User-Role": "parent"},
    )
    assert r_forbidden.status_code == 403, r_forbidden.text

    body = {
        "region_id": "rgn_test", "region_code": "test",
        "region_name": "测试区域", "sort": 99,
    }
    r = client.post("/api/admin/region", json=body, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["region_code"] == "test"

    # 重复 region_id / region_code 均 409
    r_dup = client.post("/api/admin/region", json=body, headers=headers)
    assert r_dup.status_code == 409, r_dup.text

    # 更新（改名）
    r2 = client.put(
        "/api/admin/region/rgn_test",
        json={**body, "region_name": "测试区域改"},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["region_name"] == "测试区域改"

    # 自设父级 400
    r3 = client.put(
        "/api/admin/region/rgn_test",
        json={**body, "parent_id": "rgn_test"},
        headers=headers,
    )
    assert r3.status_code == 400, r3.text

    # 删除 + 重复删除 404
    r4 = client.delete("/api/admin/region/rgn_test", headers=headers)
    assert r4.status_code == 204, r4.text
    r5 = client.delete("/api/admin/region/rgn_test", headers=headers)
    assert r5.status_code == 404, r5.text


def test_region_delete_guard_bound_tenants(client):
    """T-AM5：删除守卫——有绑定租户时 400（tenant_count 同步 +1），
    解绑后才可删（租户 region_id 是 T-AM8 数据作用域锚点，不可留孤儿）。"""
    headers = {"X-User-Id": "u_admin", "X-User-Role": "admin"}
    _login(client, "admin@tes.edu")
    body = {"region_id": "rgn_g", "region_code": "guard", "region_name": "守卫区"}
    assert client.post("/api/admin/region", json=body, headers=headers).status_code == 201

    # 绑定一个学校到该区域
    r = client.post(
        "/api/admin/tenant",
        json={"tenant_id": "t_guard", "name": "守卫学校", "region_id": "rgn_g"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # 计数随绑定 +1
    rows = client.get("/api/admin/region", headers=headers).json()
    row = [x for x in rows if x["region_id"] == "rgn_g"][0]
    assert row["tenant_count"] == 1, row

    # 有绑定租户 → 400
    r2 = client.delete("/api/admin/region/rgn_g", headers=headers)
    assert r2.status_code == 400, r2.text
    assert "1 tenants" in r2.json()["detail"]

    # 解绑（改学校区域）后删除成功
    r3 = client.put(
        "/api/admin/tenant/t_guard",
        json={"tenant_id": "t_guard", "name": "守卫学校", "region_id": None},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    r4 = client.delete("/api/admin/region/rgn_g", headers=headers)
    assert r4.status_code == 204, r4.text
