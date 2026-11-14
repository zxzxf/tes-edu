"""模块9：合规与内容安全审核。

教研/管理员对入库内容做合规初审；承接缺失项 #8（合规 P0 横切）。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 compliance_items）。
2026-11-14：**接通真实生产者**（迁移 0022）——录题、采集入库、网关敏感词
拦截都会落待审项；列表按租户过滤；审核动作留痕；补内网事件入口。

此前线上「合规审核列表没有数据」的根因不是接口报错，而是
``GET /compliance`` 返回 200 []：生产库 0 行、全仓库没有任何业务链路会写这张表。
"""
from datetime import datetime, timezone
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import (
    FALLBACK_TENANT,
    is_platform_user,
    require_role,
    write_tenant_id,
)
from app.models.schemas import ComplianceEventIn, ComplianceItem
from app.services import compliance_queue, ops
from app.services.scope import resolve_read_scope

router = APIRouter(prefix="/compliance", tags=["compliance"])

_ROLES = (Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)
#: 状态白名单。``reviewed`` 是旧前端用过的「通过」写法，归一到 approved。
_REVIEW_STATUSES = {"approved": "approved", "reviewed": "approved", "rejected": "rejected"}


@router.get("", response_model=List[ComplianceItem])
async def list_items(
    status: str = Query(None, pattern="^(pending|approved|rejected|reviewed)$"),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """待审列表。admin 全量；region_admin 按本区域全部租户；其余**只看本租户**。"""
    scope = await resolve_read_scope(user, db)
    tenant_id = scope["tenant"] if scope["mode"] == "tenant" else None
    tenant_ids = scope.get("tenants") if scope["mode"] == "tenants" else None
    return await ops.list_compliance(
        db, status=status, tenant_id=tenant_id, tenant_ids=tenant_ids
    )


@router.post("", response_model=ComplianceItem)
async def create_item(
    item: ComplianceItem,
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """手工送审。租户只取登录态，请求体里的 tenant_id 一律不认。"""
    data = item.model_dump(
        exclude={"id", "reviewed_by", "reviewed_at", "created_at"}
    )
    data.update(
        tenant_id=write_tenant_id(user),
        status="pending",
        ref_type=data.get("ref_type") or "manual",
        source=data.get("source") or "manual",
        dedup_key=data.get("dedup_key") or f"manual:{uuid4().hex}",
    )
    return await ops.create_compliance(db, data)


@router.put("/{item_id}/review")
async def review(
    item_id: int,
    status: str = Query(..., pattern="^(approved|rejected|reviewed)$"),
    user=Depends(require_role(*_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """审核结论（通过/驳回）。非平台级角色**不能审别租户的待审项**。"""
    obj = await ops.get_compliance(db, item_id)
    if not obj:
        raise HTTPException(404, "item not found")
    if (
        not is_platform_user(user)
        and obj.tenant_id
        and obj.tenant_id != write_tenant_id(user)
    ):
        raise HTTPException(403, "cannot review another tenant's item")
    await ops.review_compliance(
        db, obj, _REVIEW_STATUSES[status], actor_id=user.get("user_id")
    )
    return {"ok": True, "id": item_id, "status": _REVIEW_STATUSES[status]}


@router.post("/events", response_model=ComplianceItem)
async def gateway_event(
    event: ComplianceEventIn,
    user=Depends(require_role(Role.ADMIN, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """网关内容安全命中 → 入队（内网直调）。

    网关 ``internal/compliance`` 中间件命中敏感词时异步 POST 到这里，
    同时注入 X-User-Id / X-User-Role / X-Tenant-Id（网关本就是这些头的
    唯一注入方；外部请求经网关时会被无条件覆盖，无法伪造）。

    **只落元数据，绝不落原始请求体**——里面可能有未成年人 PII。
    按「租户 + 方法 + 路径 + 命中词 + 当天」去重，避免同一违规请求刷屏。
    """
    tenant_id = event.tenant_id or FALLBACK_TENANT
    keyword = (event.keyword or "").strip()[:64]
    path = (event.path or "")[:120]
    today = datetime.now(timezone.utc).date().isoformat()
    dedup_key = f"gateway:{event.method}:{path}:{keyword}:{today}"[:160]
    who = event.user_id or "unknown"
    content = (
        f"{event.method} {path} 命中敏感词「{keyword}」；提交人 {who}。"
        " 请求已被网关拒绝（403），内容未落业务库，请复核词表与来源。"
    )
    obj = await compliance_queue.enqueue(
        db,
        tenant_id=tenant_id,
        dedup_key=dedup_key,
        content=content,
        item_type="网关拦截",
        ref_type="gateway_reject",
        source="gateway",
    )
    await db.commit()
    if obj is None:
        # 幂等命中：不报错，网关 fire-and-forget 不该因重复事件重试
        return ComplianceItem(
            id=-1, tenant_id=tenant_id, type="网关拦截", content=content,
            status="pending", dedup_key=dedup_key, source="gateway",
        )
    return obj
