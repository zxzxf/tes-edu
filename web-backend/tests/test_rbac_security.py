"""T-AM8 安全校验集成测试（sqlite 内存库，不连生产库）。

覆盖总表 T-AM8 验收项中本轮落地部分：
- 区域过滤：region_admin 只见本辖区学校；跨区建/改 403
- 租户隔离：school 只见本校
- 越权 403：非 admin 调菜单管理接口；按钮级权限码 require_perm 放行/拒绝
- 脱敏：周报自由文本中的手机号被掩码（结构字段 student_id 保留）

降级口径（总表已注明）：家长↔学生绑定表属 P4，落地前学生级隔离为租户/区域
粗粒度；MCP 厂商 A/B 互读属 T-AM6（未启动），本文件不覆盖。
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import jwt_utils
from app.models import rbac_models as m
from app.models.course_kb import Base
from app.models.ops import StudentEvent
from main import app


async def _async_noop(*args, **kwargs):
    return None


@pytest.fixture()
def client():
    # 临时 RSA 私钥 + sqlite 内存库（StaticPool 共享连接）
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
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

            from app.services import rbac_seed

            pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
            await rbac_seed.seed_rbac(db, pwd.hash("demo1234"))

            # 三所学校：两所在华东（u_region_admin 辖区），一所在华北
            for tid, name, region in [
                ("t_east_a", "华东一中", "rgn_east"),
                ("t_east_b", "华东二中", "rgn_east"),
                ("t_north_a", "华北一中", "rgn_north"),
            ]:
                db.add(m.SysTenant(tenant_id=tid, name=name, region_id=region))

            # school 用户（属 t_east_a）+ teacher 用户（属 t_east_a）
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

            # 周报脱敏用例：题干夹带手机号
            db.add(StudentEvent(
                student_id="student_sec", event_type="solve", subject="数学",
                question="应用题：爸爸手机号 13812345678，月租多少钱？",
                occurred_at=datetime.now(timezone.utc),
            ))
            await db.commit()

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


# ---------------- 区域过滤 ----------------
def test_region_admin_sees_only_own_region_tenants(client):
    r = client.get("/api/admin/tenant", headers=_h("u_region_admin", "region_admin"))
    assert r.status_code == 200, r.text
    ids = {t["tenant_id"] for t in r.json()}
    assert ids == {"t_east_a", "t_east_b"}  # 辖区（华东）内；华北不可见


def test_region_admin_cannot_create_tenant_in_other_region(client):
    r = client.post(
        "/api/admin/tenant",
        json={"tenant_id": "t_bad", "name": "跨区校", "region_id": "rgn_north"},
        headers=_h("u_region_admin", "region_admin"),
    )
    assert r.status_code == 403, r.text


def test_region_admin_create_defaults_to_own_region(client):
    r = client.post(
        "/api/admin/tenant",
        json={"tenant_id": "t_east_c", "name": "华东三中"},
        headers=_h("u_region_admin", "region_admin"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["region_id"] == "rgn_east"  # 未传区域 → 落本辖区


def test_region_admin_cannot_update_other_region_tenant(client):
    r = client.put(
        "/api/admin/tenant/t_north_a",
        json={"tenant_id": "t_north_a", "name": "改名"},
        headers=_h("u_region_admin", "region_admin"),
    )
    assert r.status_code == 403, r.text


def test_admin_sees_all_tenants(client):
    r = client.get("/api/admin/tenant", headers=_h("u_admin", "admin"))
    assert r.status_code == 200
    assert {t["tenant_id"] for t in r.json()} == {"t_east_a", "t_east_b", "t_north_a"}


# ---------------- 租户隔离 ----------------
def test_school_sees_only_own_tenant(client):
    r = client.get("/api/admin/tenant", headers=_h("u_school", "school", tenant="t_east_a"))
    assert r.status_code == 200, r.text
    assert {t["tenant_id"] for t in r.json()} == {"t_east_a"}


# ---------------- 越权 403 ----------------
def test_teacher_cannot_list_menus(client):
    r = client.get("/api/admin/menu", headers=_h("u_teacher", "teacher"))
    assert r.status_code == 403, r.text


def test_parent_cannot_list_menus(client):
    r = client.get("/api/admin/menu", headers=_h("u_parent", "parent"))
    assert r.status_code == 403, r.text


# ---------------- 按钮级权限码（require_perm 后端强制） ----------------
def test_require_perm_denies_then_grants_menu_write(client):
    # 未授权：teacher 无 system:menu:add → 403
    r = client.post(
        "/api/admin/menu",
        json={"menu_id": "m_sec", "menu_name": "安全测试菜单", "menu_type": "MENU"},
        headers=_h("u_teacher", "teacher"),
    )
    assert r.status_code == 403, r.text

    # admin 给 role_teacher 授予"新建菜单"按钮码
    r = client.put(
        "/api/admin/role/role_teacher/menus",
        json={"menu_ids": ["m_ops_knowledge", "m_sys_menu_add"]},
        headers=_h("u_admin", "admin"),
    )
    assert r.status_code == 204, r.text

    # 授权后：新建放行（201）
    r = client.post(
        "/api/admin/menu",
        json={"menu_id": "m_sec", "menu_name": "安全测试菜单", "menu_type": "MENU"},
        headers=_h("u_teacher", "teacher"),
    )
    assert r.status_code == 201, r.text

    # 但有 add 无 delete：删除仍 403（按钮级精细生效）
    r = client.delete("/api/admin/menu/m_sec", headers=_h("u_teacher", "teacher"))
    assert r.status_code == 403, r.text

    # admin 有 *:*，删除放行
    r = client.delete("/api/admin/menu/m_sec", headers=_h("u_admin", "admin"))
    assert r.status_code == 204, r.text


# ---------------- 脱敏 ----------------
def test_weekly_report_masks_phone_in_free_text(client):
    r = client.get(
        "/api/admin/reports/weekly",
        params={"student_id": "student_sec"},
        headers=_h("u_teacher", "teacher"),
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "13812345678" not in body, "原始手机号不应出现在周报中"
    assert "138****5678" in body, "手机号应按前3后4脱敏"
    assert r.json()["student_id"] == "student_sec", "结构字段 student_id 不脱敏"
