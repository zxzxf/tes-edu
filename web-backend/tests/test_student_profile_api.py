"""M0-3 接口契约：教师维护 / 批量导入 / 学生查自己年级（sqlite 真跑）。"""
import asyncio

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.course_kb import Base


async def _noop(*args, **kwargs):
    return None


@pytest.fixture()
def client(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    from app.core import jwt_utils

    old = jwt_utils.JWT_PRIVATE_KEY
    jwt_utils.JWT_PRIVATE_KEY = pem

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as s:
            yield s

    async def bootstrap():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(bootstrap())

    import app.core.db as db_mod
    import app.services.course_kb as course_kb_mod
    import app.services.rbac_seed as rbac_seed_mod

    db_mod.engine = engine
    db_mod.SessionLocal = factory
    course_kb_mod.init_course_kb = _noop
    rbac_seed_mod.init_rbac = _noop

    from main import app
    from app.core.db import get_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    jwt_utils.JWT_PRIVATE_KEY = old
    asyncio.run(engine.dispose())


TEACHER = {"X-User-Id": "teacher@tes.edu", "X-User-Role": "teacher",
           "X-Tenant-Id": "school_pilot_01"}
STUDENT = {"X-User-Id": "student@tes.edu", "X-User-Role": "student",
           "X-Tenant-Id": "school_pilot_01"}


def test_teacher_can_upsert_and_list(client):
    r = client.put("/api/admin/students/profiles/s1",
                   json={"student_id": "s1", "grade": "高2"}, headers=TEACHER)
    assert r.status_code == 200, r.text
    assert r.json()["grade"] == "高二"

    r = client.get("/api/admin/students/profiles", headers=TEACHER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [x["student_id"] for x in body["items"]] == ["s1"]
    assert body["counts"]["高二"] == 1


def test_upsert_rejects_unknown_grade(client):
    r = client.put("/api/admin/students/profiles/s2",
                   json={"student_id": "s2", "grade": "大学"}, headers=TEACHER)
    assert r.status_code == 400, r.text


def test_student_cannot_write_profiles(client):
    r = client.put("/api/admin/students/profiles/s3",
                   json={"student_id": "s3", "grade": "高三"}, headers=STUDENT)
    assert r.status_code == 403, r.text


def test_bulk_reports_bad_rows_without_dropping_good_ones(client):
    r = client.post(
        "/api/admin/students/profiles/bulk",
        json={"profiles": [
            {"student_id": "s1", "grade": "高三"},
            {"student_id": "s2", "grade": "四年级"},
            {"student_id": "s1", "grade": "高二"},
        ]},
        headers=TEACHER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 2, body


def test_upload_csv_accepts_chinese_headers_and_bom(client):
    csv_text = "\ufeff学号,年级\ns10,高1\ns11,高3\ns12,未知\n"
    r = client.post(
        "/api/admin/students/profiles/upload",
        files={"file": ("roster.csv", csv_text.encode("utf-8"), "text/csv")},
        headers=TEACHER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 2, body
    assert body["errors"][0]["student_id"] == "s12"


def test_student_reads_own_grade_defaults_when_missing(client):
    r = client.get("/api/student/profile/me", headers=STUDENT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grade"] == "高三"
    assert body["fallback"] is True
