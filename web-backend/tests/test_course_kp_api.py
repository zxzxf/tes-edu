"""M5-3/M5-4 B 端联调回归：知识点导入必须真落库，解析必须带 code。

2026-11-11 137 生产联调实测：``POST /api/admin/course/kp-nodes`` 漏了
``await db.commit()``，服务层返回 ``created=1`` 但 get_db 的会话在请求结束
时直接 close（未提交写入被回滚）——页面提示「导入成功」，draft 列表却为空，
点「通过」直接 404。本文件用 HTTP + sqlite 真库把整条闭环锁死。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import ops  # noqa: F401  注册 KpNode / KnowledgePoint 到同一 Base
from app.models.course_kb import Base

SCHOOL = {
    "X-User-Id": "u_pilot_school",
    "X-User-Role": "school",
    "X-Tenant-Id": "school_pilot_01",
}
TEACHER = {
    "X-User-Id": "teacher@tes.edu",
    "X-User-Role": "teacher",
    "X-Tenant-Id": "school_pilot_01",
}
CODE = "BLI20261111A"


async def _noop(*args, **kwargs):
    return None


@pytest.fixture()
def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    # resolve_kp_codes 的原生 SQL 用了 PG 的 btrim()，sqlite 需注册同名函数。
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_function(
            "btrim", 1, lambda s: s.strip() if isinstance(s, str) else s
        )

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as s:
            yield s

    async def bootstrap():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # 生产 PG 的 kb_knowledge_points 由迁移补过 code/importance 两列，
            # ORM 模型未映射（只读表）；sqlite 建表后手动补齐，供
            # resolve_kp_codes / review_kp_node 的原生 SQL 使用。
            await conn.exec_driver_sql(
                "ALTER TABLE kb_knowledge_points ADD COLUMN code VARCHAR(64)"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE kb_knowledge_points ADD COLUMN importance VARCHAR(16)"
            )

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
    asyncio.run(engine.dispose())


def _payload():
    return [{
        "code": CODE, "name": "【B端联调20261111】候选A",
        "parent_code": None, "subject": "数学", "source": "crawled",
    }]


def test_upsert_commits_then_review_cycle_is_visible(client):
    r = client.post("/api/admin/course/kp-nodes", json=_payload(), headers=SCHOOL)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    # 关键回归点：新请求 / 新会话必须能看到刚才的写入（漏 commit 时为 0 行）
    r = client.get("/api/admin/course/kp-nodes?status=draft", headers=SCHOOL)
    assert r.status_code == 200, r.text
    assert CODE in [x["code"] for x in r.json()]

    r = client.post(f"/api/admin/course/kp-nodes/{CODE}/review",
                    json={"status": "published"}, headers=SCHOOL)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"
    assert r.json()["published_to_kb"] is True

    r = client.get("/api/admin/course/kp-nodes?status=published", headers=SCHOOL)
    assert CODE in [x["code"] for x in r.json()]

    r = client.post(f"/api/admin/course/kp-nodes/{CODE}/review",
                    json={"status": "draft"}, headers=SCHOOL)
    assert r.status_code == 200 and r.json()["status"] == "draft"


def test_reimport_is_idempotent(client):
    client.post("/api/admin/course/kp-nodes", json=_payload(), headers=SCHOOL)
    r = client.post("/api/admin/course/kp-nodes", json=_payload(), headers=SCHOOL)
    assert r.status_code == 200
    assert r.json()["created"] == 0
    assert r.json()["skipped"] == 1
    r = client.get("/api/admin/course/kp-nodes?status=draft", headers=SCHOOL)
    assert len([x for x in r.json() if x["code"] == CODE]) == 1


def test_overlong_code_is_rejected_at_api_boundary(client):
    bad = _payload()
    bad[0]["code"] = "x" * 33
    r = client.post("/api/admin/course/kp-nodes", json=bad, headers=SCHOOL)
    assert r.status_code == 422, r.text


def test_parse_route_emits_codes_for_all_three_layers(client):
    raw = "# 第一章 数列\n## 第一节 等差数列\n等差数列的通项公式\n"
    r = client.post("/api/admin/course/parse",
                    json={"text": raw, "source_type": "textbook"}, headers=TEACHER)
    assert r.status_code == 200, r.text
    body = r.json()
    ch = body["chapters"][0]
    assert ch["code"] and len(ch["code"]) <= 32
    assert ch["sections"][0]["code"] and len(ch["sections"][0]["code"]) <= 32
    assert ch["sections"][0]["points"][0]["code"]


def test_teacher_can_import_but_cannot_delete_source(client):
    r = client.post("/api/admin/course/kp-nodes", json=_payload(), headers=TEACHER)
    assert r.status_code == 200, r.text
    r = client.delete("/api/admin/course/sources/999999", headers=TEACHER)
    assert r.status_code == 403, r.text
