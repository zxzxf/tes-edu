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


def test_login_returns_user_id_for_student_sync(client):
    """T-WB1：登录必须回 user_id——App 端拿它当错题/背诵同步的 student_id。

    服务端对 student 角色强制用网关注入的 X-User-Id 作 student_id，所以这里
    回的流水号必须与 JWT 里的 sub 一致，否则客户端同步到的是另一份数据。
    """
    import base64
    import json as _json

    r = client.post("/login", json={"username": "student@tes.edu", "password": "demo1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "student@tes.edu"
    # JWT payload 第二段就是 claims，不必验签（这里只核对登录响应与令牌一致）。
    # 签发用的是 uid 而不是标准 sub（见 jwt_utils.create_token）。
    payload = body["access_token"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = _json.loads(base64.urlsafe_b64decode(payload))
    assert claims["uid"] == body["user_id"]
    assert claims["tenant_id"] == body["tenant_id"] == "school_pilot_01"


def test_login_still_mounted_under_admin_prefix(client):
    """/api/admin/login 兼容保留（B 端管理面行为不变）。"""
    r = client.post("/api/admin/login", json={"username": "teacher@tes.edu", "password": "demo1234"})
    assert r.status_code == 200, r.text


def test_login_trims_username_whitespace(client):
    """复制账号带首尾空格不应误报 401；签发身份必须是 trim 后的账号。

    线上实测：`"pilot@school01.edu "` 原样查库命中不到 sys_user → 401，
    去掉空格同一口令 200。用户名 trim 后所有客户端（后台/App/API 工具）都受益。
    """
    r = client.post("/login", json={"username": "  student@tes.edu  ", "password": "demo1234"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_id"] == "student@tes.edu"
    assert body["role"] == "student"
    assert body["tenant_id"] == "school_pilot_01"


def test_login_does_not_trim_password(client):
    """只 trim 用户名，密码里的空格是有效字符，不能被悄悄去掉。"""
    r = client.post("/login", json={"username": " student@tes.edu ", "password": " demo1234 "})
    assert r.status_code == 401


def test_login_db_lookup_receives_trimmed_username(client, monkeypatch):
    """DB 分支同样拿 trim 后的名字查 sys_user，而不是原始带空格串。"""
    from app.api import auth

    seen = []

    async def fake_lookup(username):
        seen.append(username)
        return None  # 回落占位库，验证参数即可

    monkeypatch.setattr(auth, "_lookup_user", fake_lookup)
    r = client.post("/login", json={"username": "\tpilot@school01.edu\n", "password": "demo1234"})
    assert r.status_code == 401  # 占位库里没有 pilot，但查库参数必须已 trim
    assert seen == ["pilot@school01.edu"]


def test_knowledge_graph_route_registered_at_learning_prefix(client):
    """/learning/knowledge-graph 已注册（未带 token 时应 401 而非 404）。"""
    r = client.get("/learning/knowledge-graph", params={"student_id": "student@tes.edu"})
    assert r.status_code == 401, r.text  # require_role 拒匿名，证明路由存在


def test_knowledge_graph_route_registered_under_admin_prefix(client):
    """/api/admin/learning/knowledge-graph 兼容保留。"""
    r = client.get("/api/admin/learning/knowledge-graph", params={"student_id": "student@tes.edu"})
    assert r.status_code == 401, r.text
