"""学校自带模型（BYOM）管理 API（W6 T-M3，挂 /api/admin 前缀）。

- GET    /byom/presets            预设供应商清单（前端下拉自动填充用）
- GET    /byom/config             当前租户模型配置（api_key 仅显末 4 位）
- PUT    /byom/config             保存/更新（SSRF 兜底校验；api_key 空=不覆盖）
- DELETE /byom/config             删除配置，回落全局 env
- POST   /byom/connectivity-test  用候选配置实调端点，返回 {ok, latency_ms, detail}

权限：本租户管理员（school）可配本租户；平台超管（admin）/区域管理员（region_admin）
可经 body.tenant_id 代配。统一 require_perm("system:byom:manage")。
后端兜底 SSRF 校验，前端校验仅作体验层。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.permission import require_perm
from app.core.rbac import Role
from app.core.security import get_current_user
from app.services import byom as svc
from app.services import byom_preset as preset_svc

router = APIRouter(tags=["byom"])
log = logging.getLogger(__name__)


# ---------------- 请求模型 ----------------

class ByomConfigIn(BaseModel):
    tenant_id: Optional[str] = None  # 仅 admin/region_admin 可代配；其余强制本租户
    provider: str = Field(default="custom", max_length=32)
    base_url: str = Field(..., min_length=1)
    api_key: Optional[str] = None  # 明文入参；空/不传 = 不覆盖已存
    model: str = Field(..., min_length=1)
    embed_base_url: Optional[str] = None
    embed_api_key: Optional[str] = None  # 空/不传 = 不覆盖；显式 "" = 清空
    embed_model: Optional[str] = None
    embed_dim: int = 1024
    model_simple: Optional[str] = None
    model_hard: Optional[str] = None
    rag_top_k: int = Field(default=4, ge=1, le=50)
    enabled: bool = True


class ConnectivityTestIn(BaseModel):
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    embed_base_url: Optional[str] = None


# ---------------- 预设清单 ----------------

@router.get("/byom/presets")
async def list_presets(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await preset_svc.list_presets(db)


# ---------------- 当前租户配置 ----------------

@router.get("/byom/config")
async def get_config(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    tenant_id = user.get("tenant_id") or ""
    if not tenant_id:
        raise HTTPException(status_code=400, detail="当前账号无租户上下文，无法读取 BYOM 配置")
    row = await svc.get_config(db, tenant_id)
    if not row:
        return {"configured": False, "tenant_id": tenant_id}
    return {"configured": True, "tenant_id": tenant_id, **svc.serialize_config_masked(row)}


# ---------------- 保存 ----------------

@router.put("/byom/config")
async def save_config(
    body: ByomConfigIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    # 租户归属：school 仅能配本租户；admin/region_admin 可经 body.tenant_id 代配
    role = user.get("role")
    tenant_id = user.get("tenant_id") or ""
    if role in (Role.ADMIN.value, Role.REGION_ADMIN.value) and body.tenant_id:
        tenant_id = body.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定目标租户（tenant_id 缺失）")

    # 首次配置必须给密钥（编辑留空 = 保留已存）
    existing = await svc.get_config(db, tenant_id)
    if not existing and not body.api_key:
        raise HTTPException(status_code=400, detail="首次配置必须填写 API Key")

    # SSRF 兜底（前端校验只是体验层）
    from app.core.ssrf import SSRFRejected, assert_safe_base_url

    try:
        assert_safe_base_url(body.base_url)
        if body.embed_base_url:
            assert_safe_base_url(body.embed_base_url)
    except SSRFRejected as e:
        raise HTTPException(status_code=400, detail=f"SSRF 拦截：{e}")

    await svc.upsert_config(
        db,
        tenant_id=tenant_id,
        provider=body.provider,
        base_url=body.base_url,
        model=body.model,
        api_key_plain=body.api_key,  # None/空 → 保持已存
        enabled=body.enabled,
        embed_base_url=body.embed_base_url or None,
        embed_api_key_plain=body.embed_api_key,  # None=保持；""=清空
        embed_model=body.embed_model or None,
        embed_dim=body.embed_dim,
        model_simple=body.model_simple or None,
        model_hard=body.model_hard or None,
        rag_top_k=body.rag_top_k,
    )
    # 尽力清 agent 侧解析缓存（未配 REDIS_URL 则靠 T-M2 短 TTL 自愈）
    await svc.invalidate_agent_cache(tenant_id)
    return {"ok": True, "tenant_id": tenant_id, "configured": True}


# ---------------- 删除（回落全局） ----------------

@router.delete("/byom/config")
async def delete_config(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    role = user.get("role")
    tenant_id = user.get("tenant_id") or ""
    if role in (Role.ADMIN.value, Role.REGION_ADMIN.value):
        # 超管删除走 query 参数；为简化，仅支持删除本账号租户（代配删除可在后续补 tenant_id 参数）
        pass
    if not tenant_id:
        raise HTTPException(status_code=400, detail="当前账号无租户上下文，无法删除 BYOM 配置")
    await svc.delete_config(db, tenant_id)
    await svc.invalidate_agent_cache(tenant_id)
    return {"ok": True, "tenant_id": tenant_id, "configured": False}


# ---------------- 连通性自检 ----------------

@router.post("/byom/connectivity-test")
async def connectivity_test(
    body: ConnectivityTestIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    result = await svc.probe_endpoint(body.base_url, body.api_key, body.model)
    return result
