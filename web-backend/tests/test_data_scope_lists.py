"""P0/P1 数据权限收口测试（sqlite 内存库，复用网关头打接口）。

P0（越权读取收紧）：
- /llm-usage：teacher 现在应 403（仅 ADMIN）。
- /reports/weekly、/p2-metrics：非平台角色忽略入参 tenant_id，强制按登录态租户聚合。

P1（region_admin 按 region 看全区）：
- /exam/papers、/exam/questions、/students/profiles：region_admin 只看本区域
  （rgn_east → t_east_a + t_east_b），school/teacher 只看本租户，且 teacher
  忽略网关注入的异租户头。
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
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import ExamPaper, StudentProfile
from main import app


async def _async_noop(*args, **kwargs):
    return None


@pytest.fixture()
def lists_client():
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

            for tid, _name, region in [
                ("t_east_a", "华东一中", "rgn_east"),
                ("t_east_b", "华东二中", "rgn_east"),
                ("t_north_a", "华北一中", "rgn_north"),
            ]:
                db.add(m.SysTenant(tenant_id=tid, name=_name, region_id=region))

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

            # 三租户各铺 1 份试卷 / 1 道题 / 1 个学生档案，供区域过滤断言
            for tid in ("t_east_a", "t_east_b", "t_north_a"):
                db.add(ExamPaper(
                    tenant_id=tid, title=f"P-{tid}", paper_type="pure_choice",
                    grade="高一", subject="数学",
                ))
                db.add(CourseQuestion(
                    subject="数学", type="choice", stem=f"Q-{tid}",
                    tenant_id=tid, status="draft",
                ))
                db.add(StudentProfile(student_id=f"stu-{tid}", tenant_id=tid, grade="高一"))

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


# ----------------------------------------------------------------- P1：区域管理员按区域

def test_region_admin_papers_see_region_only(lists_client):
    r = lists_client.get("/api/admin/exam/papers", headers=_h("u_region_admin", "region_admin"))
    assert r.status_code == 200, r.text
    titles = {p["title"] for p in r.json()}
    assert {"P-t_east_a", "P-t_east_b"} <= titles
    assert "P-t_north_a" not in titles


def test_region_admin_questions_see_region_only(lists_client):
    r = lists_client.get("/api/admin/exam/questions", headers=_h("u_region_admin", "region_admin"))
    assert r.status_code == 200, r.text
    stems = {q["stem"] for q in r.json()}
    assert {"Q-t_east_a", "Q-t_east_b"} <= stems
    assert "Q-t_north_a" not in stems


def test_region_admin_profiles_see_region_only(lists_client):
    r = lists_client.get("/api/admin/students/profiles", headers=_h("u_region_admin", "region_admin"))
    assert r.status_code == 200, r.text
    sids = {it["student_id"] for it in r.json()["items"]}
    assert {"stu-t_east_a", "stu-t_east_b"} <= sids
    assert "stu-t_north_a" not in sids


def test_school_papers_see_own_tenant_only(lists_client):
    r = lists_client.get("/api/admin/exam/papers", headers=_h("u_school", "school", tenant="t_east_a"))
    assert r.status_code == 200, r.text
    titles = {p["title"] for p in r.json()}
    assert titles == {"P-t_east_a"}


def test_teacher_papers_ignore_tenant_header(lists_client):
    # 即使网关注入 t_north_a，teacher 仍只按 sys_user.tenant_id=t_east_a
    r = lists_client.get("/api/admin/exam/papers", headers=_h("u_teacher", "teacher", tenant="t_north_a"))
    assert r.status_code == 200, r.text
    titles = {p["title"] for p in r.json()}
    assert titles == {"P-t_east_a"}


# ----------------------------------------------------------------- P0：越权读取收紧

def test_llm_usage_teacher_forbidden(lists_client):
    r = lists_client.get("/api/admin/llm-usage", headers=_h("u_teacher", "teacher", tenant="t_east_a"))
    assert r.status_code == 403


def test_llm_usage_admin_ok(lists_client):
    r = lists_client.get("/api/admin/llm-usage", headers=_h("u_admin", "admin"))
    assert r.status_code == 200


def test_reports_teacher_forced_own_tenant(lists_client, monkeypatch):
    from app.services import reports as reports_svc

    captured = {}
    async def fake(db, student_id, period_days, tenant_id):
        captured["tenant_id"] = tenant_id
        return {}
    monkeypatch.setattr(reports_svc, "compute_weekly_report", fake)

    # 传别校 tenant_id，teacher 必须被忽略，强制用登录态租户 t_east_a
    r = lists_client.get(
        "/api/admin/reports/weekly?student_id=s1&tenant_id=t_north_a",
        headers=_h("u_teacher", "teacher", tenant="t_east_a"),
    )
    assert r.status_code == 200, r.text
    assert captured["tenant_id"] == "t_east_a"


def test_metrics_teacher_forced_own_tenant(lists_client, monkeypatch):
    from app.services import p2_metrics as metrics_svc

    captured = {}
    async def fake(db, student_id, tenant_id, from_dt, to_dt, subject):
        captured["tenant_id"] = tenant_id
        return {}
    monkeypatch.setattr(metrics_svc, "compute_p2_metrics_for", fake)

    r = lists_client.get(
        "/api/admin/p2-metrics?student_id=s1&tenant_id=t_north_a",
        headers=_h("u_teacher", "teacher", tenant="t_east_a"),
    )
    assert r.status_code == 200, r.text
    assert captured["tenant_id"] == "t_east_a"
