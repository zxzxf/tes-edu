"""学校自带模型（BYOM）管理 API（W6 T-M3，挂 /api/admin 前缀；T-M5 列表化改造）。

- GET    /byom/presets                预设供应商清单（前端下拉自动填充用）
- GET    /byom/configs                配置列表（超管看全部租户；学校仅本租户）
- GET    /byom/config                 当前租户模型配置（api_key 仅显末 4 位）
- PUT    /byom/config                 保存/更新（SSRF 兜底校验；api_key 空=不覆盖）
- POST   /byom/config/reveal          明文回显密钥（需权限 + 审计日志）
- DELETE /byom/config?tenant_id=      删除配置，回落全局 env
- POST   /byom/connectivity-test      用候选配置实调端点，返回 {ok, latency_ms, detail}
- POST   /byom/configs/{tid}/test     用库里已存配置实调端点（列表行内连通测试）

权限：本租户管理员（school）可配本租户；平台超管（admin）/区域管理员（region_admin）
可经 body.tenant_id 代配。写操作统一 require_perm("system:byom:manage")。
后端兜底 SSRF 校验，前端校验仅作体验层。
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


class RevealKeyIn(BaseModel):
    tenant_id: Optional[str] = None  # 仅 admin/region_admin 可指定他人租户


# ---------------- 租户归属解析 ----------------

def _target_tenant(user: dict, requested: Optional[str]) -> str:
    """解析目标租户：school 强制本租户；admin/region_admin 可代指定。"""
    role = user.get("role")
    base = user.get("tenant_id") or ""
    if requested:
        if role in (Role.ADMIN.value, Role.REGION_ADMIN.value):
            return requested
        if requested != base:
            raise HTTPException(status_code=403, detail="不可访问其他租户的模型配置")
    return base


# ---------------- 预设清单 ----------------

@router.get("/byom/presets")
async def list_presets(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await preset_svc.list_presets(db)


# ---------------- 配置列表（T-M5：后台表格化展示） ----------------

@router.get("/byom/configs")
async def list_configs(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """模型配置列表。超管/区域管理员看全部租户；学校管理员只看到本租户那一行。"""
    role = user.get("role")
    tenant_id = user.get("tenant_id") or ""
    if role in (Role.ADMIN.value, Role.REGION_ADMIN.value):
        rows = await svc.list_configs(db)
    else:
        if not tenant_id:
            raise HTTPException(status_code=400, detail="当前账号无租户上下文")
        one = await svc.get_config(db, tenant_id)
        rows = [one] if one else []

    preset_names = {}
    try:
        for p in await preset_svc.list_presets(db):
            preset_names[p.get("provider")] = p.get("display_name") or p.get("provider")
    except Exception:  # noqa: BLE001 预设表异常不阻断列表
        pass

    return [
        {
            **svc.serialize_config_masked(r),
            "provider_display": preset_names.get(r.provider, r.provider),
        }
        for r in rows
    ]


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
    tenant_id = _target_tenant(user, body.tenant_id)
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


# ---------------- 明文回显（T-M5：列表页「反显」按钮） ----------------

@router.post("/byom/config/reveal")
async def reveal_key(
    body: RevealKeyIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    """解密回显明文密钥。

    安全口径：需 system:byom:manage 权限 + 租户归属校验；每次调用留审计日志
    （只记租户与操作者，不记 key 本体）。主密钥轮换导致解密失败时返回 400，
    不吐半截明文。
    """
    tenant_id = _target_tenant(user, body.tenant_id)
    row = await svc.get_config(db, tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="该租户尚未配置 BYOM")
    try:
        keys = svc.reveal_keys(row)
    except Exception as e:  # noqa: BLE001 解密失败（密钥轮换/密文损坏）
        raise HTTPException(status_code=400, detail=f"密钥解密失败：{str(e)[:120]}")
    log.info(
        "byom key revealed: tenant=%s by user=%s role=%s",
        tenant_id, user.get("user_id"), user.get("role"),
    )
    return {"tenant_id": tenant_id, **keys}


# ---------------- 删除（回落全局） ----------------

@router.delete("/byom/config")
async def delete_config(
    tenant_id: Optional[str] = Query(None, description="超管删指定租户；留空=本租户"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    target = _target_tenant(user, tenant_id)
    if not target:
        raise HTTPException(status_code=400, detail="当前账号无租户上下文，无法删除 BYOM 配置")
    await svc.delete_config(db, target)
    await svc.invalidate_agent_cache(target)
    return {"ok": True, "tenant_id": target, "configured": False}


# ---------------- 连通性自检 ----------------

@router.post("/byom/connectivity-test")
async def connectivity_test(
    body: ConnectivityTestIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    result = await svc.probe_endpoint(body.base_url, body.api_key, body.model)
    return result


@router.post("/byom/configs/{tenant_id}/test")
async def test_stored_config(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_perm("system:byom:manage")),
):
    """列表行内连通测试：直接用库里已存（解密后）的密钥与模型实调端点。"""
    target = _target_tenant(user, tenant_id)
    row = await svc.get_config(db, target)
    if not row:
        raise HTTPException(status_code=404, detail="该租户尚未配置 BYOM，无法测试")
    try:
        api_key = svc.decrypt_api_key(row.api_key_enc) if row.api_key_enc else ""
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"密钥解密失败：{str(e)[:120]}")
    if not api_key:
        raise HTTPException(status_code=400, detail="该配置缺少 API Key")
    return await svc.probe_endpoint(row.base_url, api_key, row.model)
