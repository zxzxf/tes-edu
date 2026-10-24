"""T-B5 路由挂载契约：网关 /login 与 /learning/ 原样转发，根路径必须可达。

背景（2026-10-24 部署踩坑）：web-backend 全部路由曾只挂 /api/admin 前缀，
而自建 Go 网关不做路径改写——网关 /login、/learning/ 路由转发的路径
在 web-backend 侧 404。修复为根路径双挂载，本测试锁定该契约。
"""
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    """注入临时 RS256 私钥（模块级缓存需显式覆盖，同 test_core 模式）。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    from app.core import jwt_utils

    old = jwt_utils.JWT_PRIVATE_KEY
    os.environ["JWT_PRIVATE_KEY"] = pem
    jwt_utils.JWT_PRIVATE_KEY = pem
    yield TestClient(app)
    jwt_utils.JWT_PRIVATE_KEY = old


def test_login_mounted_at_root(client):
    """POST /login 在根路径可达（网关 /login 路由 mode 为空不鉴权）。"""
    r = client.post("/login", json={"username": "student@tes.edu", "password": "demo1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "student"
    assert body["access_token"]


def test_login_still_mounted_under_admin_prefix(client):
    """/api/admin/login 兼容保留（B 端管理面行为不变）。"""
    r = client.post("/api/admin/login", json={"username": "teacher@tes.edu", "password": "demo1234"})
    assert r.status_code == 200, r.text


def test_knowledge_graph_route_registered_at_learning_prefix(client):
    """/learning/knowledge-graph 已注册（未带 token 时应 401 而非 404）。"""
    r = client.get("/learning/knowledge-graph", params={"student_id": "student@tes.edu"})
    assert r.status_code == 401, r.text  # require_role 拒匿名，证明路由存在


def test_knowledge_graph_route_registered_under_admin_prefix(client):
    """/api/admin/learning/knowledge-graph 兼容保留。"""
    r = client.get("/api/admin/learning/knowledge-graph", params={"student_id": "student@tes.edu"})
    assert r.status_code == 401, r.text
