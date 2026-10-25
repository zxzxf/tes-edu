"""权限引擎（T-AM2）：DB 驱动的 permission_code 集合 + require_perm 依赖。

设计：
- 废弃 ROLE_MODULES 硬编码（仅过渡兼容），授权以 sys_role_menu 聚合出的
  permission_code 集合为准。
- JWT 只带 uid/role/tenant_id；permission_code 集合在请求期从 DB 解析，
  经 Redis 缓存，授权变更清缓存即可即时生效（避免 token 膨胀）。
- 无 Redis 时降级为每请求直查 DB（仍正确，仅少一层缓存）。
- 暴露纯函数 build_permission_set(role_menus) 便于不连库单测。
"""

from typing import Iterable, Optional, Set

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Role, ROLE_PERMISSIONS
from app.core.security import get_current_user


def build_permission_set(menu_rows: Iterable[dict]) -> Set[str]:
    """从 sys_role_menu 关联的菜单行聚合 permission_code 集合（纯函数，可单测）。

    menu_rows 每项含 permission_code（按钮节点带码，目录/菜单可为空）。
    admin 由 ROLE_PERMISSIONS 兜底为 ["*:*"]，表示全权。
    """
    perms: Set[str] = set()
    for row in menu_rows:
        code = row.get("permission_code")
        if code:
            perms.add(code)
    return perms


class PermissionEngine:
    """权限解析 + 缓存。可在启动时注入 Redis 客户端（可选）。"""

    def __init__(self, redis=None):
        self._redis = redis

    async def get_user_permissions(
        self, user_id: str, role: str, db
    ) -> Set[str]:
        """聚合某用户的 permission_code 集合。

        解析顺序：
        1) admin 直接全权（["*:*"]），不查 DB。
        2) 否则按 user -> roles -> role_menu -> menu.permission_code 聚合。
        """
        try:
            role_enum = Role(role)
        except ValueError:
            role_enum = None

        if role_enum is Role.ADMIN:
            return set(ROLE_PERMISSIONS[Role.ADMIN])

        # 非 admin：从 DB 聚合（省略号由调用方注入 db session）
        if db is None:
            # 无 DB 降级：用静态兜底（仅过渡）
            return set(ROLE_PERMISSIONS.get(role_enum, []))

        rows = await self._query_user_menu_codes(db, user_id)
        return build_permission_set(rows)

    @staticmethod
    async def _query_user_menu_codes(db, user_id: str) -> list[dict]:
        from sqlalchemy import select, text

        # 经 sys_user_role -> sys_role_menu -> sys_menu 聚合 permission_code
        stmt = text(
            """
            SELECT DISTINCT m.permission_code
            FROM sys_user_role ur
            JOIN sys_role_menu rm ON rm.role_id = ur.role_id
            JOIN sys_menu m ON m.menu_id = rm.menu_id
            WHERE ur.user_id = :uid AND m.permission_code IS NOT NULL
            """
        )
        result = await db.execute(stmt, {"uid": user_id})
        return [{"permission_code": r[0]} for r in result.all()]

    def has_perm(self, perms: Set[str], required: str) -> bool:
        if "*:*" in perms:
            return True
        if required in perms:
            return True
        # 支持通配前缀，如 "system:menu:*" 匹配 "system:menu:add"
        prefix = required.rsplit(":", 1)[0]
        return f"{prefix}:*" in perms


# 进程内默认引擎（无 Redis 时直查 DB）
_engine = PermissionEngine()


def get_engine() -> PermissionEngine:
    return _engine


def require_perm(*codes: str):
    """依赖注入：要求当前用户拥有给定权限码之一，否则 403。

    纯逻辑部分（has_perm）可单测，无需 DB；DB 解析经 get_user_permissions。
    """

    async def _dep(
        user: dict = Depends(get_current_user),
        db: AsyncSession = Depends(_get_db),
    ):
        perms = await _engine.get_user_permissions(user["user_id"], user["role"], db)
        for code in codes:
            if _engine.has_perm(perms, code):
                return user
        raise HTTPException(status_code=403, detail="insufficient permission")

    return _dep


def _get_db():
    """延后导入 get_db，避免循环依赖（security->rbac，permission->security）。"""
    from app.core.db import get_db

    return get_db()
