"""#28 学生端「看视频讲解」取流路由（2026-11-15）。

背景：App 原来直接把后端返回的**相对** `video_url` 丢给 VideoView，
既无 scheme/host，又发不出 Authorization 头（网关 /api/student/ 是 jwt_c）。
修复后 App 用带 Bearer 的 Retrofit 调 `/explanation-video/{id}/stream`，
下载到私有缓存再用本地路径播放。

本文件覆盖新路由与历史直链的鉴权 / 租户 / 状态复校、MinIO 与本地取流回退。
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import explanation_video as ev
from app.core.db import get_db
from app.models.explanation_video import ExplanationVideo

PILOT = "school_pilot_01"
VID = "3257a0247d8d453d81e6d3561a42d164"
MP4 = b"\x00\x00\x00\x18ftypmp42fake-video-bytes"


def _headers(role: str = "student", tenant: str | None = PILOT) -> dict:
    h = {"X-User-Id": "u_student", "X-User-Role": role}
    if tenant is not None:
        h["X-Tenant-Id"] = tenant
    return h


def _row(status: str = "done", tenant: str = PILOT) -> ExplanationVideo:
    return ExplanationVideo(
        id=VID,
        tenant_id=tenant,
        content_hash="a" * 16,
        question_text="已知函数求极值",
        video_url=f"/api/student/explanation-videos/{VID}.mp4",
        duration_sec=4.0,
        status=status,
    )


class _Result:
    def __init__(self, row=None, rows=None, total=0):
        self.row = row
        self.rows = rows or []
        self.total = total

    def scalar_one_or_none(self):
        return self.row

    def scalar_one(self):
        return self.total

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)


class _FakeDB:
    """只实现本模块用到的 execute；row / rows / 顺序结果由用例注入。"""

    def __init__(self, row=None, rows=None, total=0, results=None):
        self.row = row
        self.rows = rows or []
        self.total = total
        self.results = results
        self.calls = 0
        self.stmts = []

    async def execute(self, stmt):
        self.calls += 1
        self.stmts.append(stmt)
        if self.results is not None:
            return self.results[min(self.calls - 1, len(self.results) - 1)]
        return _Result(self.row, self.rows, self.total)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """搭一个只挂 explanation_video 两个 router 的最小 app，隔离 DB / MinIO / 本地目录。"""

    def build(row=None, minio_bytes=MP4, local_bytes=None, db=None):
        monkeypatch.setattr(
            ev, "settings", SimpleNamespace(explanation_video_dir=str(tmp_path))
        )

        async def _fetch(object_name):
            return minio_bytes, "video/mp4"

        monkeypatch.setattr(ev.object_storage, "fetch_video", _fetch)
        if local_bytes is not None:
            (tmp_path / f"{VID}.mp4").write_bytes(local_bytes)

        db = db or _FakeDB(row)
        app = FastAPI()
        app.include_router(ev.student_router, prefix="/api/student")
        app.include_router(ev.admin_router, prefix="/api/admin")
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app), db

    return build


STREAM = f"/api/student/explanation-video/{VID}/stream"
LEGACY = f"/api/student/explanation-videos/{VID}.mp4"
ADMIN_STREAM = f"/api/admin/explanation-video/{VID}/stream"


def test_stream_本租户已出片_返回mp4(harness):
    client, db = harness(_row())
    r = client.get(STREAM, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("video/mp4")
    assert r.content == MP4
    assert "private" in r.headers["cache-control"]
    assert db.calls == 1  # 取流前确实查了行（复校租户/状态）


def test_stream_缺身份头_401(harness):
    client, _ = harness(_row())
    r = client.get(STREAM)
    assert r.status_code == 401


@pytest.mark.parametrize("path", [STREAM, LEGACY])
def test_stream_跨租户_两条路径都404(harness, path):
    """历史直链以前无鉴权、无租户校验，登录后拼任意 id 即可跨租户取流。"""
    client, _ = harness(_row(tenant="demo"))
    r = client.get(path, headers=_headers())
    assert r.status_code == 404


def test_stream_未出片_404(harness):
    client, _ = harness(_row(status="processing"))
    assert client.get(STREAM, headers=_headers()).status_code == 404


def test_stream_任务不存在_404(harness):
    client, _ = harness(None)
    assert client.get(STREAM, headers=_headers()).status_code == 404


def test_stream_MinIO缺对象_回退本地文件(harness):
    local = b"legacy-local-mp4-bytes"
    client, _ = harness(_row(), minio_bytes=None, local_bytes=local)
    r = client.get(STREAM, headers=_headers())
    assert r.status_code == 200, r.text
    assert r.content == local


def test_stream_MinIO与本地都缺_404(harness):
    client, _ = harness(_row(), minio_bytes=None, local_bytes=None)
    assert client.get(STREAM, headers=_headers()).status_code == 404


def test_by_question_返回任务id供取流(harness):
    client, _ = harness(_row())
    r = client.get(
        "/api/student/explanation-video/by-question",
        params={"question": "已知函数求极值"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == VID
    assert r.json()["status"] == "done"


def test_by_question_无视频时id为null(harness):
    client, _ = harness(None)
    r = client.get(
        "/api/student/explanation-video/by-question",
        params={"question": "没有视频的题"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] is None
    assert body["status"] == "none"
    assert body["video_url"] is None
    assert body["content_hash"]


def test_admin_stream_教师跨租户_403(harness):
    """重构共用 _load_video_bytes 后，教师端原有的行级隔离不能退化。"""
    client, _ = harness(_row(tenant="demo"))
    r = client.get(ADMIN_STREAM, headers=_headers(role="teacher", tenant=PILOT))
    assert r.status_code == 403


def test_admin_stream_超管可跨租户取流(harness):
    client, _ = harness(_row(tenant="demo"))
    r = client.get(ADMIN_STREAM, headers=_headers(role="admin", tenant="platform"))
    assert r.status_code == 200, r.text
    assert r.content == MP4


# ---------------- 学生端「发现视频」：列表 + 题干兜底匹配 ----------------

def _cand(text: str, tenant: str = PILOT) -> ExplanationVideo:
    r = _row(tenant=tenant)
    r.question_text = text
    return r


def test_by_question_排版不同_归一化兜底命中(harness):
    """同一题多一个空格/全角标点，逐字 hash 不命中也要能找到视频。"""
    db = _FakeDB(results=[
        _Result(None),
        _Result(rows=[_cand("已知函数求极值。")]),
    ])
    client, _ = harness(db=db)
    r = client.get(
        "/api/student/explanation-video/by-question",
        params={"question": "已知函数，求极值"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == VID
    assert body["match"] in ("normalized", "fuzzy")


def test_by_question_相似度太低_不误配别的题(harness):
    db = _FakeDB(results=[
        _Result(None),
        _Result(rows=[_cand("【IVH联通验证190356】1+1等于几？")]),
    ])
    client, _ = harness(db=db)
    r = client.get(
        "/api/student/explanation-video/by-question",
        params={"question": "求函数 f(x)=x² 的导数并说明依据"},
        headers=_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] is None
    assert r.json()["match"] == "none"


def test_student_list_返回本租户已出片视频(harness):
    db = _FakeDB(rows=[_cand("1+1等于几？"), _cand("求导数")], total=2)
    client, _ = harness(db=db)
    r = client.get("/api/student/explanation-video/list", headers=_headers())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert set(body["items"][0]) == {
        "id", "question_text", "knowledge_point", "duration_sec", "created_at"
    }
    # 查询必须带租户 + done + 有 URL，不能把别校/未出片任务漏给学生
    sql = str(db.stmts[1].compile(compile_kwargs={"literal_binds": True}))
    assert "school_pilot_01" in sql
    assert "done" in sql
    assert "video_url" in sql


def test_student_list_缺身份头_401(harness):
    client, _ = harness(db=_FakeDB(rows=[], total=0))
    assert client.get("/api/student/explanation-video/list").status_code == 401


def test_student_list_空列表_返回空items(harness):
    client, _ = harness(db=_FakeDB(rows=[], total=0))
    r = client.get("/api/student/explanation-video/list", headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 0, "items": []}
