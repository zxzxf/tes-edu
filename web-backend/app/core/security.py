"""鉴权工具：解析从 Go 网关透传过来的用户身份。

设计说明：
- 真正校验 JWT 签名、限流、审计都在 Go 网关完成。
- Web 后端处于内网，默认信任网关注入的 X-User-Id / X-User-Role 头。
- 若需双校验（纵深防御），可在此用 python-jose 再用同一 RSA 公钥验一次签名（与网关共用密钥对）。
"""
from typing import Optional

from fastapi import Depends, Header, HTTPException

from app.core.rbac import Role


def get_current_user(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
    x_user_region: Optional[str] = Header(None, alias="X-User-Region"),
) -> dict:
    if not x_user_id or not x_user_role:
        raise HTTPException(
            status_code=401,
            detail="missing user headers from gateway (X-User-Id / X-User-Role)",
        )
    # 直接调用（未经 FastAPI 注入）时，未传参的默认值是 Header 标记对象而非 None，
    # 这里统一归一为 str|None，避免标记对象泄漏到调用方（T-AM8 加固）。
    tenant_id = x_tenant_id if isinstance(x_tenant_id, str) else None
    region_id = x_user_region if isinstance(x_user_region, str) else None
    return {
        "user_id": x_user_id,
        "role": x_user_role,
        # 网关已验证签 JWT 后注入（Header.Set 无条件覆盖，防伪造）；
        # web-backend 此前丢弃该头，导致租户/区域过滤失效（T-AM8 修复）。
        "tenant_id": tenant_id,
        # region_id 不在 JWT claim 内：优先信网关注入头，否则由
        # app/services/scope.py::resolve_scope 回查 sys_user.region_id。
        "region_id": region_id,
    }


def require_role(*roles: Role):
    """依赖注入：要求当前用户属于给定角色之一，否则 403。"""

    def _dep(user: dict = Depends(get_current_user)):
        if user["role"] not in [r.value for r in roles]:
            raise HTTPException(status_code=403, detail="insufficient role")
        return user

    return _dep


# ---------------------------------------------------------------- 租户口径
#: 平台级用户（admin / region_admin）在 sys_user 里 tenant_id 为空，签发 JWT 时
#: 用 "platform" 占位（见 auth.py T-AM7）。这个占位值在业务表里不存在任何数据行，
#: 若读侧照常按它过滤，超管打开题目目录/采集任务页就会看到一片空白。
PLATFORM_TENANT = "platform"
PLATFORM_ROLES = frozenset({Role.ADMIN.value, Role.REGION_ADMIN.value})
#: 网关漏注入 X-Tenant-Id 时的兜底租户，与 questions / exam / course 保持一致。
FALLBACK_TENANT = "school_pilot_01"


def is_platform_user(user: dict) -> bool:
    """是否平台级角色（超管/区域管理员）或持有平台占位租户。"""
    return (
        user.get("role") in PLATFORM_ROLES
        or (user.get("tenant_id") or "") == PLATFORM_TENANT
    )


def read_tenant_id(user: dict) -> Optional[str]:
    """读侧租户过滤值：平台级返回 ``None`` 表示跨租户只读；其余返回本租户。

    重要：调用方拿到 None 时必须**不加 where 条件**，不能写成
    ``column == None``（SQLAlchemy 会编译成 IS NULL，反而一条都查不到）。
    网关漏注入租户头时仍按试点校兜底——只有显式识别出的平台级角色才放开，
    一次配置遗漏不会把别校数据整表暴露出去。
    """
    if is_platform_user(user):
        return None
    return user.get("tenant_id") or FALLBACK_TENANT


def write_tenant_id(user: dict) -> str:
    """写侧租户：保持严格隔离，平台级占位也只作用于自身，绝不跨租户写入。"""
    return user.get("tenant_id") or FALLBACK_TENANT
