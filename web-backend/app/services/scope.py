"""数据作用域解析（T-AM8 安全校验：区域过滤 / 租户隔离）。

背景：region_id 不在 JWT claim 里（网关只注入 X-User-Id/X-User-Role/X-Tenant-Id），
region_admin 的辖区必须回查 sys_user.region_id。本模块把该解析收敛成一处，
供各 API 在查询前下推过滤条件，避免每个接口各写一遍且写法漂移。

作用域口径（与《后台权限与系统集成任务计划书》一致）：
- admin          平台超管：不过滤（全量）。
- region_admin   区域管理员：仅本 region_id（回查 sys_user.region_id）。
- school         学校：仅本 tenant_id（优先网关注入头，回落 sys_user.tenant_id）。
- teacher/parent 暂按其所带 tenant_id 过滤（家长↔学生绑定表属 P4，落地前
                 学生级隔离只能做到租户粗粒度，见总表 T-AM8 降级口径）。

resolve_scope 返回 dict：
    region_id   当前用户辖区（无则 None，表示不限区域）
    tenant_id   当前用户所属租户（无则 None）
    tenant_ids  辖区下的租户集合（region_admin 用；None 表示不限）
    scoped      是否受限（False 仅 admin）
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Role
from app.core.security import FALLBACK_TENANT
from app.models import rbac_models as m


async def resolve_scope(user: dict, db: AsyncSession) -> dict:
    role = user.get("role")
    user_id = user.get("user_id")
    scope = {
        "region_id": None,
        "tenant_id": None,
        "tenant_ids": None,
        "scoped": role != Role.ADMIN.value,
    }

    if role == Role.ADMIN.value:
        return scope

    # 回查 sys_user：补 region_id / tenant_id（头优先，DB 兜底）
    row = None
    if user_id:
        row = (await db.execute(
            select(m.SysUser).where(m.SysUser.user_id == user_id)
        )).scalar_one_or_none()

    if role == Role.REGION_ADMIN.value:
        # DB 行 region 为权威（与 list_users 一致），网关注入头仅作兜底。
        scope["region_id"] = (row.region_id if row else None) or user.get("region_id")
        if scope["region_id"]:
            ids = (await db.execute(
                select(m.SysTenant.tenant_id).where(m.SysTenant.region_id == scope["region_id"])
            )).scalars().all()
            scope["tenant_ids"] = list(ids)
        return scope

    # school / teacher / parent：DB 行租户为权威，忽略网关注入头，避免被误归到别校
    # （此前 teacher@tes.edu 因 DB 租户漂移 + 头优先，错误看到别校数据）。
    scope["tenant_id"] = (row.tenant_id if row else None) or user.get("tenant_id") or FALLBACK_TENANT
    # 注意：单租户角色不要往 tenant_ids 塞 [tenant_id]，否则 resolve_read_scope
    # 会把"单租户"误判成"区域管理员 IN 列表"模式（语义不同）。
    scope["tenant_ids"] = None
    return scope


async def tenant_ids_in_region(db: AsyncSession, region_id: str) -> List[str]:
    """某区域下全部租户 ID（region_admin 查询下推用）。"""
    ids = (await db.execute(
        select(m.SysTenant.tenant_id).where(m.SysTenant.region_id == region_id)
    )).scalars().all()
    return list(ids)


async def resolve_read_scope(user: dict, db: AsyncSession) -> dict:
    """读侧租户作用域描述符，供管理端列表接口下推过滤。

    与 ``resolve_scope`` 同源，但把结果收敛成三种模式，便于各接口统一处理：
      - ``{"mode": "all"}``                        -> admin，不加任何过滤（全量）
      - ``{"mode": "tenant", "tenant": tid}``      -> school/teacher/parent，按本租户
      - ``{"mode": "tenants", "tenants": [...]}`` -> region_admin，按本区域全部租户 IN

    scoped 但两边都解析不出时退化为兜底租户（绝不放行全量，避免配置遗漏裸奔）。
    """
    scope = await resolve_scope(user, db)
    if not scope["scoped"]:
        return {"mode": "all"}
    if scope["tenant_ids"]:
        return {"mode": "tenants", "tenants": list(scope["tenant_ids"])}
    if scope["tenant_id"]:
        return {"mode": "tenant", "tenant": scope["tenant_id"]}
    return {"mode": "tenant", "tenant": FALLBACK_TENANT}


def where_tenant(stmt, column, rs: dict):
    """把 ``resolve_read_scope`` 的结果下推到 SQLAlchemy 查询（读侧统一口径）。"""
    mode = rs.get("mode")
    if mode == "all":
        return stmt
    if mode == "tenants":
        ids = rs.get("tenants") or ["__never_match__"]
        return stmt.where(column.in_(ids))
    return stmt.where(column == rs.get("tenant"))


def assert_region_allowed(user: dict, target_region_id: Optional[str], scope_region_id: Optional[str]) -> None:
    """region_admin 跨区写操作的统一校验（不允许则 403）。

    纯逻辑部分（不抛异常时返回 None），便于单测；抛异常由调用方转为 HTTPException
    或直接使用 raise_http_forbidden。
    """
    if user.get("role") != Role.REGION_ADMIN.value or not scope_region_id:
        return None
    if target_region_id and target_region_id != scope_region_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="无权跨区域操作")
    return None
