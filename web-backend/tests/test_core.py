"""web-backend 核心逻辑回归测试（不连库、不依赖外部服务）。

覆盖：RBAC 角色矩阵、网关透传身份鉴权、RS256 令牌签发/验证、
请求契约校验、学情时间解析。所有用例均为纯逻辑，CI 中无 DB 即可运行。
"""
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# ---------- RBAC 角色矩阵 ----------
def test_role_enum_values():
    from app.core.rbac import Role

    assert Role.ADMIN.value == "admin"
    assert Role.PARENT.value == "parent"
    assert Role.STUDENT.value == "student"


def test_role_modules_access():
    from app.core.rbac import ROLE_MODULES, Role

    assert ROLE_MODULES[Role.ADMIN] == "*"          # 超级管理员全开
    assert ROLE_MODULES[Role.PARENT] == ["reports"]  # 家长仅周报
    assert ROLE_MODULES[Role.STUDENT] == []          # 学生 C 端无后台模块


# ---------- 网关透传身份鉴权 ----------
def test_get_current_user_missing_headers():
    from app.core.security import get_current_user

    with pytest.raises(Exception):  # FastAPI HTTPException(401)
        get_current_user(x_user_id=None, x_user_role=None)


def test_get_current_user_ok():
    from app.core.security import get_current_user

    user = get_current_user(x_user_id="u100", x_user_role="teacher")
    assert user == {"user_id": "u100", "role": "teacher"}


def test_require_role_allows():
    from app.core.security import require_role
    from app.core.rbac import Role

    dep = require_role(Role.ADMIN, Role.TEACHER)
    user = {"user_id": "u1", "role": "teacher"}
    assert dep(user=user) == user


def test_require_role_forbids():
    from app.core.security import require_role
    from app.core.rbac import Role

    dep = require_role(Role.ADMIN)
    with pytest.raises(Exception):  # HTTPException(403)
        dep(user={"user_id": "u1", "role": "parent"})


# ---------- RS256 令牌签发/验证 ----------
@pytest.fixture
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub


def test_create_token_roundtrip(rsa_keys):
    from jose import jwt as jose_jwt

    priv, pub = rsa_keys
    from app.core import jwt_utils

    os.environ["JWT_PRIVATE_KEY"] = priv
    jwt_utils.JWT_PRIVATE_KEY = priv  # 覆盖模块级缓存

    token = jwt_utils.create_token("u200", "researcher")
    claims = jose_jwt.decode(token, pub, algorithms=["RS256"])
    assert claims["uid"] == "u200"
    assert claims["role"] == "researcher"
    assert "iat" in claims and "exp" in claims


def test_create_token_requires_key():
    from app.core import jwt_utils

    jwt_utils.JWT_PRIVATE_KEY = ""  # 模拟未配置
    with pytest.raises(RuntimeError):
        jwt_utils.create_token("u1", "admin")


# ---------- 请求契约校验 ----------
def test_knowledge_point_defaults():
    from app.models.schemas import KnowledgePoint

    kp = KnowledgePoint(subject="数学", grade="高一", title="函数单调性", content="...")
    assert kp.tags == []  # 默认空标签


def test_login_request_required():
    from pydantic import ValidationError

    from app.models.schemas import LoginRequest

    with pytest.raises(ValidationError):
        LoginRequest(username="only-user")  # 缺 password
    ok = LoginRequest(username="a", password="b")
    assert ok.username == "a"


def test_weekly_report_schema():
    from app.models.schemas import WeeklyReport

    r = WeeklyReport(student_id="s1", week="2026-W43", summary="本周掌握度提升")
    assert r.weak_points == []


# ---------- 学情时间解析 ----------
def test_parse_dt_variants():
    from datetime import datetime, timezone

    from app.services.learning import _parse_dt

    now = datetime.now(timezone.utc)
    assert _parse_dt(now) is now                       # 原样返回 datetime
    assert isinstance(_parse_dt("2026-10-23T01:02:03"), datetime)  # ISO 解析
    assert _parse_dt("not-a-date") is None             # 非法串 -> None
    assert _parse_dt(None) is None                     # None -> None
