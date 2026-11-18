"""M1-6 答题附件端点回归：上传闸门（类型/大小）、取流鉴权、token 校验。"""
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import exam_attachments
from app.core.config import settings

STUDENT = {"X-User-Id": "stu-9", "X-User-Role": "student", "X-Tenant-Id": "school-1"}
OTHER = {"X-User-Id": "t1", "X-User-Role": "teacher", "X-Tenant-Id": "school-1"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "exam_attachment_dir", str(tmp_path / "att"))
    app = FastAPI()
    app.include_router(exam_attachments.router, prefix="/api/student")
    # 单测无网关：require_role 读的身份头已由测试客户端注入（与 rbac 集成测试同法）
    return TestClient(app)


def _jpg():
    # 1x1 合法 JPEG 头即可——端点只看 content_type/大小，不解析像素
    return io.BytesIO(b"\xff\xd8\xff\xe0" + b"0" * 100)


def test_upload_rejects_bad_type(client):
    r = client.post(
        "/api/student/exam-attachments",
        headers=STUDENT,
        files={"file": ("a.gif", io.BytesIO(b"GIF89a"), "image/gif")},
    )
    assert r.status_code == 415


def test_upload_rejects_oversize(client):
    big = io.BytesIO(b"\xff\xd8" + b"0" * (10 * 1024 * 1024 + 10))
    r = client.post(
        "/api/student/exam-attachments",
        headers=STUDENT,
        files={"file": ("a.jpg", big, "image/jpeg")},
    )
    assert r.status_code == 413


def test_upload_then_fetch_roundtrip(client):
    r = client.post(
        "/api/student/exam-attachments",
        headers=STUDENT,
        files={"file": ("a.jpg", _jpg(), "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("/api/student/exam-attachments/")
    g = client.get(url, headers=STUDENT)
    assert g.status_code == 200
    assert g.headers["content-type"] == "image/jpeg"
    assert g.headers["cache-control"] == "private, no-store"
    # 批改教师可取图（工单里嵌的 [图](url) 要能点开看，M2-6 闭环）
    assert client.get(url, headers=OTHER).status_code == 200
    # 但教师不能上传（闸门仍只对学生开放）
    p = client.post(
        "/api/student/exam-attachments", headers=OTHER,
        files={"file": ("a.jpg", _jpg(), "image/jpeg")},
    )
    assert p.status_code == 403


def test_get_rejects_malformed_token(client):
    r = client.get(
        "/api/student/exam-attachments/../../etc/passwd%00", headers=STUDENT
    )
    assert r.status_code in (404, 422)  # 路由不匹配 404 / 路径非法 422，绝不落文件
