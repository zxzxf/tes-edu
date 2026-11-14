"""平台占位租户（exam 域）+ 激活角色权限码 + 批改按钮码真正生效（2026-11-14）。

三处线上实测根因：
1. exam.py 的 ``user.tenant_id or FALLBACK`` 兜不住超管 JWT 的非空占位 ``platform``，
   线上 ``GET /exam/papers`` 返回 0 条、``GET /exam/papers/{id}`` 一律 404，
   而 7 张卷都在 school_pilot_01。
2. permission.py::_query_user_menu_codes 只按 user_id 聚合，把用户所有角色的
   按钮码取并集。线上 u_pilot_school 同时挂 region_admin + school，切成 school
   后仍持有 region_admin 的 system:tenant:* / exam:generate —— 切角色是假生效。
3. grading:submit/review 只挂旧模块5接口，工单判定/改判/解冻走 TeacherDep，
   撤销按钮码并不能真的拦住改分。
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import exam, grading
from app.core import permission as perm_mod
from app.core.db import get_db
from app.core.security import FALLBACK_TENANT, PLATFORM_TENANT
from app.models.ops import ExamPaper
from app.services import rbac_seed

PILOT = "school_pilot_01"


def _headers(role: str, tenant: str | None = None) -> dict:
    h = {"X-User-Id": "u_test", "X-User-Role": role}
    if tenant is not None:
        h["X-Tenant-Id"] = tenant
    return h


class _EmptyResult:
    def scalar(self): return 0
    def scalar_one_or_none(self): return None
    def scalar_one(self): return None
    def scalars(self): return self
    def all(self): return []
    def __iter__(self): return iter([])


class _CaptureDB:
    """收集 SQL 绑定参数，断言业务表 where 里没有 platform 占位。"""

    def __init__(self):
        self.stmts = []

    async def execute(self, stmt, params=None):
        self.stmts.append(stmt)
        return _EmptyResult()

    @property
    def params(self):
        out = []
        for stmt in self.stmts:
            try:
                out.extend(stmt.compile().params.values())
            except Exception:  # noqa: BLE001
                pass
        return out


# ---------------------------------------------------------------- exam：平台占位
@pytest.fixture
def exam_client():
    app = FastAPI()
    app.include_router(exam.router, prefix="/api/admin")
    db = _CaptureDB()
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.clear()


def test_admin_试卷列表全量不过滤租户(exam_client):
    client, db = exam_client
    r = client.get("/api/admin/exam/papers", headers=_headers("admin", PLATFORM_TENANT))
    assert r.status_code == 200, r.text
    assert r.json() == []
    # admin 全量：不加任何租户 where；platform 占位更不该进业务表
    assert PLATFORM_TENANT not in db.params, "平台占位不该进业务表 where 条件"
    assert PILOT not in db.params, "admin 全量不应被压成兜底租户"


def test_school_只看自己租户(exam_client):
    client, db = exam_client
    r = client.get("/api/admin/exam/papers", headers=_headers("school", "demo"))
    assert r.status_code == 200, r.text
    assert "demo" in db.params


class _PaperResult:
    def __init__(self, paper):
        self._paper = paper

    def scalar_one_or_none(self):
        return self._paper


class _PaperDB:
    def __init__(self, paper):
        self.paper = paper

    async def execute(self, stmt, params=None):
        return _PaperResult(self.paper)


def _paper(tenant_id):
    return ExamPaper(id=9, tenant_id=tenant_id, title="卷", paper_type="mixed", published=True)


def test_admin_可打开兜底租户的卷_不再404():
    user = {"role": "admin", "tenant_id": PLATFORM_TENANT, "user_id": "u_admin"}
    p = asyncio.run(exam._paper_or_404(_PaperDB(_paper(PILOT)), 9, user))
    assert p.id == 9


def test_别校卷仍一律404():
    user = {"role": "school", "tenant_id": "demo", "user_id": "u_school"}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(exam._paper_or_404(_PaperDB(_paper(PILOT)), 9, user))
    assert exc.value.status_code == 404


def test_老卷tenant为空仍放行():
    user = {"role": "school", "tenant_id": "demo", "user_id": "u_school"}
    p = asyncio.run(exam._paper_or_404(_PaperDB(_paper(None)), 9, user))
    assert p.id == 9


def test_网关漏注入租户头也归一到兜底租户():
    user = {"role": "admin", "tenant_id": None, "user_id": "u_admin"}
    p = asyncio.run(exam._paper_or_404(_PaperDB(_paper(PILOT)), 9, user))
    assert p.id == 9


# ---------------------------------------------------------------- permission：激活角色
class _Row:
    def __init__(self, code):
        self.code = code

    def __getitem__(self, idx):
        return self.code


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _PermDB:
    """按 (uid, role) 返回该角色的按钮码，模拟 sys_role_menu 关联结果。"""

    CODES = {
        "region_admin": ["exam:generate", "system:tenant:add", "system:tenant:delete"],
        "school": ["system:user:add", "system:user:delete", "grading:review"],
    }

    def __init__(self):
        self.calls = []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        assert "r.role_code = :role" in sql, "SQL 必须按激活角色过滤，不能做多角色并集"
        self.calls.append(dict(params or {}))
        return _RowsResult([_Row(c) for c in self.CODES.get((params or {}).get("role"), [])])


def test_查询必须带上激活角色参数():
    db = _PermDB()
    rows = asyncio.run(perm_mod.PermissionEngine._query_user_menu_codes(db, "u_pilot", "school"))
    codes = {r["permission_code"] for r in rows}
    assert codes == {"system:user:add", "system:user:delete", "grading:review"}
    assert db.calls[0]["role"] == "school"
    assert db.calls[0]["uid"] == "u_pilot"


@pytest.mark.parametrize("role,expect_present,expect_absent", [
    ("region_admin", "system:tenant:add", "system:user:add"),
    ("school", "system:user:add", "system:tenant:add"),
])
def test_切成低权限角色后不再带出别的角色的码(monkeypatch, role, expect_present, expect_absent):
    async def no_cache(*a, **kw):
        return None

    monkeypatch.setattr(perm_mod.cache, "get_json", no_cache)
    monkeypatch.setattr(perm_mod.cache, "set_json", no_cache)

    engine = perm_mod.PermissionEngine()
    perms = asyncio.run(engine.get_user_permissions("u_pilot", role, _PermDB()))
    assert expect_present in perms
    assert expect_absent not in perms, f"{role} 不该拿到别的角色的按钮码"


def test_admin_仍走全权短路():
    perms = asyncio.run(perm_mod.PermissionEngine().get_user_permissions("u_admin", "admin", _PermDB()))
    assert perms == {"*:*"}


# ---------------------------------------------------------------- grading：按钮码真正生效
@pytest.fixture
def grading_perm_client(monkeypatch):
    async def no_cache(*a, **kw):
        return None

    monkeypatch.setattr(perm_mod.cache, "get_json", no_cache)
    monkeypatch.setattr(perm_mod.cache, "set_json", no_cache)

    db = _PermDB()

    class _WriteDB(_PermDB):
        async def execute(self, stmt, params=None):
            # 权限码查询走 _PermDB；工单/护栏的 ORM select 返回空 → 过权限后 404
            if "permission_code" in str(stmt):
                return await super().execute(stmt, params)
            return _EmptyResult()

    write_db = _WriteDB()
    app = FastAPI()
    app.include_router(grading.router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: write_db
    app.dependency_overrides[perm_mod._get_db] = lambda: write_db
    with TestClient(app) as c:
        yield c, write_db
    app.dependency_overrides.clear()


def test_写接口无按钮码403_有按钮码才进入业务(grading_perm_client, monkeypatch):
    client, db = grading_perm_client
    # school 在 seed 里已补授 grading:review；这里把它的码临时撤掉模拟「授权撤销」
    monkeypatch.setattr(db, "CODES", {
        **_PermDB.CODES,
        "school": ["system:user:add"],
        "teacher": [],
    })
    r = client.post("/api/admin/grading/tickets/1/claim", headers=_headers("teacher", PILOT))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "insufficient permission"

    monkeypatch.setattr(db, "CODES", {
        **_PermDB.CODES,
        "teacher": ["grading:review"],
    })
    r2 = client.post("/api/admin/grading/tickets/1/claim", headers=_headers("teacher", PILOT))
    assert r2.status_code == 404, "过了按钮码校验才应进入工单查询（空库 → 404）"


def test_读接口不需要按钮码(grading_perm_client, monkeypatch):
    client, db = grading_perm_client
    monkeypatch.setattr(db, "CODES", {**_PermDB.CODES, "teacher": []})
    r = client.get("/api/admin/grading/tickets", headers=_headers("teacher", PILOT))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_school_seed补授批改按钮码_避免新死链():
    pairs = {(r["role_id"], r["menu_id"]) for r in rbac_seed.build_seed_role_menus()}
    for mid in ("m_ops_grading_submit", "m_ops_grading_review"):
        assert ("role_school", mid) in pairs
        assert ("role_teacher", mid) in pairs
        assert ("role_region_admin", mid) not in pairs
