"""MCP 厂商与数据服务实例管理 API（T-AM6，挂在 /api/admin 前缀下）。

- GET/POST/PUT/DELETE /vendor                厂商注册表 CRUD
- GET/POST/PUT/DELETE /vendor/{id}/instances  每厂商独立数据服务实例 CRUD
- POST /vendor/{id}/test                      连通性测试（解密凭证 → 实调 base_url）

安全口径（与计划书 §五 一致）：
- 读仅 admin：厂商与凭证属平台级机密，region_admin 也不可见；
- 写统一 `system:vendor:manage` 按钮码（require_perm；admin 恒有 *:*）；
- 凭证只写不回显：`credential` / `connection` 是入参明文，加密后落
  `credential_enc` / `connection_enc`（AES-256-GCM，见 vendor_crypto.py），
  响应模型刻意不含密文字段，只回 `has_credential` 布尔提示；
- 实例端点强制 vendor_id 路径隔离：instance.vendor_id 与路径不一致一律 404
  （不泄露他厂商实例存在性）；
- 删除守卫：厂商下仍有实例时 400（实例是数据服务载体，不可留孤儿）。
"""

import time
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.permission import require_perm
from app.core.rbac import Role
from app.core.security import require_role
from app.models import rbac_models as m
from app.services import vendor_crypto

router = APIRouter(tags=["vendor"])

TEST_TIMEOUT_SECONDS = 5.0


# ---------------- 请求/响应模型 ----------------
# 注意：响应模型刻意不含 credential/connection 及其密文——明文凭证的
# 唯一去向是加密落库，任何回显路径（含调试）都不开。
class VendorIn(BaseModel):
    vendor_id: str
    vendor_name: str
    vendor_type: str  # aliyun/tencent/ark/deepseek/custom...
    base_url: Optional[str] = None
    auth_type: Optional[str] = None  # apikey/oauth/aksk
    credential: Optional[str] = None  # 明文凭证（仅入参；加密后落 credential_enc）
    status: int = 1
    region_id: Optional[str] = None
    tenant_id: Optional[str] = None


class VendorOut(BaseModel):
    vendor_id: str
    vendor_name: str
    vendor_type: str
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    status: int
    region_id: Optional[str] = None
    tenant_id: Optional[str] = None
    has_credential: bool = False  # 是否已存凭证（前端据此提示"保存即覆盖"）
    instance_count: int = 0  # 名下数据服务实例数（删除守卫依据）


class InstanceIn(BaseModel):
    instance_id: str
    service_name: str
    data_namespace: str  # schema / keyspace / 存储前缀（逻辑隔离分区键）
    isolation_mode: str = "logical"  # logical / physical
    connection: Optional[str] = None  # 明文连接串/凭证（仅入参，加密后落库）
    status: int = 1


class InstanceOut(BaseModel):
    instance_id: str
    vendor_id: str
    service_name: str
    data_namespace: str
    isolation_mode: str
    status: int
    has_connection: bool = False


def _vendor_out(v: m.SysServiceVendor, instance_count: int = 0) -> VendorOut:
    return VendorOut(
        vendor_id=v.vendor_id,
        vendor_name=v.vendor_name,
        vendor_type=v.vendor_type,
        base_url=v.base_url,
        auth_type=v.auth_type,
        status=v.status,
        region_id=v.region_id,
        tenant_id=v.tenant_id,
        has_credential=bool(v.credential_enc),
        instance_count=instance_count,
    )


def _instance_out(i: m.McpServiceInstance) -> InstanceOut:
    return InstanceOut(
        instance_id=i.instance_id,
        vendor_id=i.vendor_id,
        service_name=i.service_name,
        data_namespace=i.data_namespace,
        isolation_mode=i.isolation_mode,
        status=i.status,
        has_connection=bool(i.connection_enc),
    )


async def _instance_counts(db: AsyncSession) -> dict:
    rows = (await db.execute(
        select(m.McpServiceInstance.vendor_id, func.count().label("n"))
        .group_by(m.McpServiceInstance.vendor_id)
    )).all()
    return {vid: n for vid, n in rows}


# ---------------- 厂商 CRUD ----------------
@router.get("/vendor", response_model=List[VendorOut])
async def list_vendors(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    """厂商列表（不含凭证；带实例计数）。仅 admin。"""
    rows = (await db.execute(
        select(m.SysServiceVendor).order_by(m.SysServiceVendor.created_at)
    )).scalars().all()
    counts = await _instance_counts(db)
    return [_vendor_out(v, counts.get(v.vendor_id, 0)) for v in rows]


@router.get("/vendor/{vendor_id}", response_model=VendorOut)
async def get_vendor(
    vendor_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    """单个厂商（编辑表单回显用；同样不含凭证）。"""
    v = (await db.execute(
        select(m.SysServiceVendor).where(m.SysServiceVendor.vendor_id == vendor_id)
    )).scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="vendor not found")
    counts = await _instance_counts(db)
    return _vendor_out(v, counts.get(v.vendor_id, 0))


@router.post("/vendor", response_model=VendorOut, status_code=201)
async def create_vendor(
    body: VendorIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    exists = (await db.execute(
        select(m.SysServiceVendor).where(m.SysServiceVendor.vendor_id == body.vendor_id)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="vendor already exists")
    data = body.model_dump()
    credential = data.pop("credential", None)
    v = m.SysServiceVendor(**data)
    if credential:
        v.credential_enc = vendor_crypto.encrypt(credential)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return _vendor_out(v)


@router.put("/vendor/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: str,
    body: VendorIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    v = (await db.execute(
        select(m.SysServiceVendor).where(m.SysServiceVendor.vendor_id == vendor_id)
    )).scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="vendor not found")
    data = body.model_dump()
    credential = data.pop("credential", None)
    data["vendor_id"] = vendor_id  # 路径优先：防止改 ID 造成引用漂移
    for k, val in data.items():
        setattr(v, k, val)
    # 凭证语义：传了非空明文才覆盖重加密；不传（None/空串）保持原密文不动——
    # 否则编辑表单不带凭证字段就会把已存凭证抹掉。
    if credential:
        v.credential_enc = vendor_crypto.encrypt(credential)
    await db.commit()
    await db.refresh(v)
    counts = await _instance_counts(db)
    return _vendor_out(v, counts.get(v.vendor_id, 0))


@router.delete("/vendor/{vendor_id}", status_code=204)
async def delete_vendor(
    vendor_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    v = (await db.execute(
        select(m.SysServiceVendor).where(m.SysServiceVendor.vendor_id == vendor_id)
    )).scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="vendor not found")
    # 删除守卫：名下仍有数据服务实例时 400（实例是数据载体，不可留孤儿）
    n = (await db.execute(
        select(func.count()).select_from(m.McpServiceInstance)
        .where(m.McpServiceInstance.vendor_id == vendor_id)
    )).scalar() or 0
    if n:
        raise HTTPException(
            status_code=400,
            detail=f"vendor has {n} instances, delete them first",
        )
    await db.delete(v)
    await db.commit()
    return None


# ---------------- 每厂商数据服务实例（vendor_id 路径强制隔离） ----------------
async def _get_vendor_or_404(db: AsyncSession, vendor_id: str) -> m.SysServiceVendor:
    v = (await db.execute(
        select(m.SysServiceVendor).where(m.SysServiceVendor.vendor_id == vendor_id)
    )).scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="vendor not found")
    return v


@router.get("/vendor/{vendor_id}/instances", response_model=List[InstanceOut])
async def list_instances(
    vendor_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role(Role.ADMIN)),
):
    await _get_vendor_or_404(db, vendor_id)
    rows = (await db.execute(
        select(m.McpServiceInstance)
        .where(m.McpServiceInstance.vendor_id == vendor_id)
        .order_by(m.McpServiceInstance.created_at)
    )).scalars().all()
    return [_instance_out(i) for i in rows]


@router.post("/vendor/{vendor_id}/instances", response_model=InstanceOut, status_code=201)
async def create_instance(
    vendor_id: str,
    body: InstanceIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    await _get_vendor_or_404(db, vendor_id)
    exists = (await db.execute(
        select(m.McpServiceInstance).where(m.McpServiceInstance.instance_id == body.instance_id)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="instance_id already exists")
    data = body.model_dump()
    connection = data.pop("connection", None)
    inst = m.McpServiceInstance(vendor_id=vendor_id, **data)
    if connection:
        inst.connection_enc = vendor_crypto.encrypt(connection)
    db.add(inst)
    await db.commit()
    await db.refresh(inst)
    return _instance_out(inst)


@router.put("/vendor/{vendor_id}/instances/{instance_id}", response_model=InstanceOut)
async def update_instance(
    vendor_id: str,
    instance_id: str,
    body: InstanceIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    await _get_vendor_or_404(db, vendor_id)
    inst = (await db.execute(
        select(m.McpServiceInstance).where(m.McpServiceInstance.instance_id == instance_id)
    )).scalar_one_or_none()
    # 路径隔离：实例不存在或不属于该厂商，一律 404（不泄露存在性）
    if not inst or inst.vendor_id != vendor_id:
        raise HTTPException(status_code=404, detail="instance not found")
    data = body.model_dump()
    connection = data.pop("connection", None)
    data["instance_id"] = instance_id  # 路径优先：防止改 ID 造成引用漂移
    for k, val in data.items():
        setattr(inst, k, val)
    if connection:
        inst.connection_enc = vendor_crypto.encrypt(connection)
    await db.commit()
    await db.refresh(inst)
    return _instance_out(inst)


@router.delete("/vendor/{vendor_id}/instances/{instance_id}", status_code=204)
async def delete_instance(
    vendor_id: str,
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    await _get_vendor_or_404(db, vendor_id)
    inst = (await db.execute(
        select(m.McpServiceInstance).where(m.McpServiceInstance.instance_id == instance_id)
    )).scalar_one_or_none()
    if not inst or inst.vendor_id != vendor_id:
        raise HTTPException(status_code=404, detail="instance not found")
    await db.delete(inst)
    await db.commit()
    return None


# ---------------- 连通性测试 ----------------
@router.post("/vendor/{vendor_id}/test")
async def test_vendor(
    vendor_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:vendor:manage")),
):
    """解密凭证实调 base_url，返回连通性结果（不泄露凭证本体）。

    - 未存凭证 / 未配 base_url → 400（前端应先保存再测试）；
    - 网络类错误 → ok=false + detail（截断），不影响厂商数据。
    """
    v = await _get_vendor_or_404(db, vendor_id)
    if not v.base_url:
        raise HTTPException(status_code=400, detail="vendor has no base_url")
    if not v.credential_enc:
        raise HTTPException(status_code=400, detail="vendor has no credential stored")
    try:
        credential = vendor_crypto.decrypt(v.credential_enc)
    except Exception as e:  # 密钥轮换/密文损坏：不吐明文，只报类型
        raise HTTPException(status_code=500, detail=f"credential decrypt failed: {type(e).__name__}")

    headers = {}
    if v.auth_type in ("apikey", "oauth") and credential:
        headers["Authorization"] = f"Bearer {credential}"
    # aksk 等需要签名的类型：仅做无签名连通探测，detail 里说明

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(v.base_url, headers=headers)
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = f"HTTP {resp.status_code}"
        if v.auth_type not in ("apikey", "oauth", None, ""):
            detail += f"（{v.auth_type} 未做签名，仅连通性探测）"
        return {
            "ok": resp.status_code < 500,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "detail": detail,
        }
    except httpx.HTTPError as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": latency_ms,
            "detail": f"{type(e).__name__}: {str(e)[:200]}",
        }
