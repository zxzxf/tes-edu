"""用户列表数据权限矩阵测试（sqlite 内存库，复用网关头打接口）。

验证 GET /api/admin/user 按角色收窄返回的 sys_user 集合：
- admin         全平台
- region_admin  当前区域下所有租户（rgn_east → t_east_a + t_east_b）
- school        当前租户（t_east_a）
- teacher       当前租户（t_east_a，只读；写接口不受此影响）
受限角色若无法定位归属，返回空集而非全量。
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
from sqlalchemy.pool import StaticPool

from app.core import jwt_utils
from app.models import rbac_models as m
from app.models.course_kb import Base
from main import app


async def _async_noop(*args, **kwargs):
    return None


@pytest.fixture()
def scope_client():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    os.environ["JWT_PRIVATE_KEY"] = pem
    jwt_utils.JWT_PRIVATE_KEY = pem

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

            from app.services import rbac_seed

            pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

            # 三所学校：两所华东（rgn_east），一所华北（rgn_north）
            for tid, name, region in [
                ("t_east_a", "华东一中", "rgn_east"),
                ("t_east_b", "华东二中", "rgn_east"),
                ("t_north_a", "华北一中", "rgn_north"),
            ]:
                db.add(m.SysTenant(tenant_id=tid, name=name, region_id=region))

            # 演示账号（school/teacher 落在 t_east_a，供租户隔离断言）
            db.add(m.SysUser(
                user_id="u_school", username="school@tes.edu",
                password_hash=pwd.hash("demo1234"), tenant_id="t_east_a",
            ))
            db.add(m.SysUserRole(user_id="u_school", role_id="role_school", tenant_id="platform"))
            db.add(m.SysUser(
                user_id="u_teacher", username="teacher@tes.edu",
                password_hash=pwd.hash("demo1234"), tenant_id="t_east_a",
            ))
            db.add(m.SysUserRole(user_id="u_teacher", role_id="role_teacher", tenant_id="platform"))

            # 分租户/区域的探测用户（带 region_id，供区域过滤断言）
            db.add(m.SysUser(user_id="u_a1", username="a1@tes.edu", password_hash=pwd.hash("demo1234"), tenant_id="t_east_a", region_id="rgn_east"))
            db.add(m.SysUser(user_id="u_a2", username="a2@tes.edu", password_hash=pwd.hash("demo1234"), tenant_id="t_east_b", region_id="rgn_east"))
            db.add(m.SysUser(user_id="u_north", username="north@tes.edu", password_hash=pwd.hash("demo1234"), tenant_id="t_north_a", region_id="rgn_north"))
            db.add(m.SysUser(user_id="u_plat", username="plat@tes.edu", password_hash=pwd.hash("demo1234"), tenant_id=None, region_id=None))
            await db.commit()

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


def _h(user_id, role, tenant=None, region=None):
    headers = {"X-User-Id": user_id, "X-User-Role": role}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    if region:
        headers["X-User-Region"] = region
    return headers


def _usernames(client, headers):
    r = client.get("/api/admin/user", headers=headers)
    assert r.status_code == 200, r.text
    return {u["username"] for u in r.json()}


def test_admin_sees_all(scope_client):
    names = _usernames(scope_client, _h("u_admin", "admin"))
    # 平台/各租户探测用户全在；admin@tes.edu 被隐藏
    assert {"a1@tes.edu", "a2@tes.edu", "north@tes.edu", "plat@tes.edu"} <= names
    assert "admin@tes.edu" not in names


def test_region_admin_sees_only_own_region(scope_client):
    # u_region_admin 种子 region_id=rgn_east，resolve_scope 回查得到
    names = _usernames(scope_client, _h("u_region_admin", "region_admin"))
    assert "a1@tes.edu" in names and "a2@tes.edu" in names      # 华东两所
    assert "north@tes.edu" not in names                          # 华北不在辖区
    assert "plat@tes.edu" not in names                            # 平台账号不归属区域


def test_school_sees_only_own_tenant(scope_client):
    names = _usernames(scope_client, _h("u_school", "school", tenant="t_east_a"))
    assert "a1@tes.edu" in names
    assert "a2@tes.edu" not in names
    assert "north@tes.edu" not in names
    assert "plat@tes.edu" not in names


def test_teacher_sees_only_own_tenant_readonly(scope_client):
    names = _usernames(scope_client, _h("u_teacher", "teacher", tenant="t_east_a"))
    assert "a1@tes.edu" in names
    assert "a2@tes.edu" not in names
    assert "north@tes.edu" not in names
    assert "plat@tes.edu" not in names


def test_teacher_menu_granted_but_write_still_403(scope_client):
    # 菜单授权：teacher 应有 m_sys_user（只读）
    menu = scope_client.get("/api/admin/menu/my", headers=_h("u_teacher", "teacher", tenant="t_east_a"))
    assert "m_sys_user" in menu.text
    # 写接口：teacher 不在 UserWriteRoleDep(ADMIN/SCHOOL) 白名单
    r = scope_client.post("/api/admin/user", headers=_h("u_teacher", "teacher", tenant="t_east_a"),
                          json={"username": "x@tes.edu", "password": "p"})
    assert r.status_code == 403


def test_teacher_ignores_tenant_header_and_sees_only_own_tenant(scope_client):
    # 即使网关注入别的租户头（如 school_pilot_01 / t_north_a），teacher 也只按
    # 自己 sys_user.tenant_id 过滤，杜绝被 FALLBACK 误归到其它学校
    names = _usernames(scope_client, _h("u_teacher", "teacher", tenant="t_north_a"))
    assert "a1@tes.edu" in names            # 属 t_east_a（teacher 真实租户）
    assert "north@tes.edu" not in names     # 头里的 t_north_a 被忽略
    assert "a2@tes.edu" not in names        # 另一租户

