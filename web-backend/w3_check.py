"""W3 后端最小集成验证（sqlite in-memory，不依赖生产 Postgres）。

覆盖：功能开关种子、范围优先级解析、平台过滤、学生端 API、项目 CRUD 隔离、
防疲劳配置覆盖生效。仅建 5 张新表，不建其他 postgres 特有表。
"""
import os

# 必须在 import app.core.db 之前设好，否则它会用默认 postgres URL 建引擎（需 asyncpg）。
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.core.permission import _get_db as perm_get_db
from app.models import student_features as sf
from app.models.course_kb import Base
from app.api import student_features as api
from app.services import rbac_seed

TABLES = [sf.FeatureSwitch, sf.FeatureSwitchOverride, sf.AntiFatigueConfig,
          sf.StudentProject, sf.StudentProjectTask]

engine = create_async_engine(
    "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def override_get_db():
    async with Session() as s:
        yield s


app = FastAPI()
app.include_router(api.student_router, prefix="/api/student")
app.include_router(api.admin_router, prefix="/api/admin")
app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[perm_get_db] = override_get_db

STU = {"X-User-Id": "stu1", "X-User-Role": "student", "X-Tenant-Id": "t1"}
STU2 = {"X-User-Id": "stu2", "X-User-Role": "student", "X-Tenant-Id": "t2"}
ADM = {"X-User-Id": "adm", "X-User-Role": "admin"}


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[t.__table__ for t in TABLES]))
    async with Session() as db:
        print("seed_features:", await rbac_seed.seed_features(db))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # 1) 默认全开
        r = await c.get("/api/student/features", params={"platform": "web"}, headers=STU)
        feats = r.json()["features"]
        assert feats == {k: True for k in
                         ["project", "anti_fatigue", "wrong_book", "group_paper",
                          "knowledge_graph", "ai_tutor"]}, feats
        print("[1] 默认 6 枚全开 OK")

        # 2) 平台过滤：project 是 web-only，app 平台下不应出现；其余 both 仍出现
        r = await c.get("/api/student/features", params={"platform": "app"}, headers=STU)
        feats_app = r.json()["features"]
        assert "project" not in feats_app, feats_app
        assert set(feats_app.keys()) == {
            "anti_fatigue", "wrong_book", "group_paper", "knowledge_graph", "ai_tutor"}, feats_app
        print("[2] 平台过滤 OK（project 仅 web 可见，app 不可见）")

        # 3) tenant 级覆盖优先级：t1 关 project -> stu1 见 False，stu2 仍 True
        r = await c.put("/api/admin/features", headers=ADM, json={
            "feature_key": "project", "scope_type": "tenant", "scope_id": "t1", "enabled": False})
        assert r.status_code == 200, r.text
        r = await c.get("/api/student/features", params={"platform": "web"}, headers=STU)
        assert r.json()["features"]["project"] is False
        r = await c.get("/api/student/features", params={"platform": "web"}, headers=STU2)
        assert r.json()["features"]["project"] is True
        print("[3] tenant 覆盖优先级 OK")

        # 4) global 覆盖：关 ai_tutor -> 所有租户 False
        r = await c.put("/api/admin/features", headers=ADM, json={
            "feature_key": "ai_tutor", "scope_type": "global", "scope_id": "", "enabled": False})
        r = await c.get("/api/student/features", params={"platform": "web"}, headers=STU2)
        assert r.json()["features"]["ai_tutor"] is False
        print("[4] global 覆盖 OK")

        # 5) 项目 CRUD + 隔离
        r = await c.post("/api/student/projects", headers=STU, json={"title": "攻克电磁感应"})
        assert r.status_code == 201, r.text
        pid = r.json()["project_id"]
        r = await c.get(f"/api/student/projects/{pid}", headers=STU)
        assert r.status_code == 200 and r.json()["title"] == "攻克电磁感应"
        r = await c.get(f"/api/student/projects/{pid}", headers=STU2)
        assert r.status_code == 404  # 跨学生隔离
        # 任务
        r = await c.post(f"/api/student/projects/{pid}/tasks", headers=STU,
                         json={"title": "任务1", "sort": 1})
        tid = r.json()["task_id"]
        r = await c.put(f"/api/student/projects/{pid}/tasks/{tid}", headers=STU, json={"done": True})
        assert r.json()["ok"] is True
        r = await c.get(f"/api/student/projects/{pid}", headers=STU)
        assert r.json()["task_done"] == 1 and abs(r.json()["progress"] - 1.0) < 1e-9
        print("[5] 项目 CRUD + 隔离 + 进度 OK")

        # 6) 防疲劳配置：默认 25 分钟；tenant t1 改为 30/enforce 生效
        r = await c.get("/api/student/anti-fatigue", headers=STU)
        assert r.json()["interval_min"] == 25, r.json()
        r = await c.put("/api/admin/anti-fatigue", headers=ADM, json={
            "scope_type": "tenant", "scope_id": "t1", "interval_min": 30, "mode": "enforce"})
        assert r.status_code == 200, r.text
        r = await c.get("/api/student/anti-fatigue", headers=STU)
        assert r.json()["interval_min"] == 30 and r.json()["mode"] == "enforce"
        # 另一租户仍默认
        r = await c.get("/api/student/anti-fatigue", headers=STU2)
        assert r.json()["interval_min"] == 25
        print("[6] 防疲劳配置覆盖生效 OK")

    print("ALL_W3_BACKEND_TESTS_PASS")


if __name__ == "__main__":
    asyncio.run(main())
