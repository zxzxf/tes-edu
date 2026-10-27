"""BYOM 租户级模型配置服务层（W6 T-M1）。

- 密钥加解密复用 app/services/vendor_crypto.py（AES-256-GCM，与厂商凭证同一主密钥
  VENDOR_CREDENTIAL_KEY），不在此重复实现加密原语；
- 明文段（api_key）只在后端内存中短暂存在，落库即密文、出响应即脱敏；
- 基础 CRUD 供 T-M3 后台 API 与 T-M2 解析共用。
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.byom import TenantModelConfig
from app.services import vendor_crypto as vc


# ---------- 密钥脱敏展示 ----------

def mask_key(plain: Optional[str]) -> str:
    """仅展示末 4 位：sk-****abcd → ****abcd；空值返回空串。"""
    if not plain:
        return ""
    if len(plain) <= 4:
        return plain
    return "****" + plain[-4:]


# ---------- 基础 CRUD（密钥以明文入参，落库前加密） ----------

async def get_config(db: AsyncSession, tenant_id: str) -> Optional[TenantModelConfig]:
    res = await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == tenant_id)
    )
    return res.scalar_one_or_none()


async def list_configs(db: AsyncSession) -> List[TenantModelConfig]:
    res = await db.execute(select(TenantModelConfig))
    return list(res.scalars().all())


async def upsert_config(
    db: AsyncSession,
    *,
    tenant_id: str,
    provider: str,
    base_url: str,
    api_key_plain: str,
    model: str,
    enabled: bool = True,
    embed_base_url: Optional[str] = None,
    embed_api_key_plain: Optional[str] = None,
    embed_model: Optional[str] = None,
    embed_dim: int = 1024,
    model_simple: Optional[str] = None,
    model_hard: Optional[str] = None,
    rag_top_k: int = 4,
) -> TenantModelConfig:
    """按 tenant_id 幂等 upsert；api_key 明文在此加密后落库，明文不落库。"""
    obj = TenantModelConfig(
        tenant_id=tenant_id,
        provider=provider,
        base_url=base_url,
        api_key_enc=vc.encrypt(api_key_plain),
        model=model,
        embed_base_url=embed_base_url,
        embed_api_key_enc=vc.encrypt(embed_api_key_plain) if embed_api_key_plain else None,
        embed_model=embed_model,
        embed_dim=embed_dim,
        model_simple=model_simple,
        model_hard=model_hard,
        enabled=enabled,
        rag_top_k=rag_top_k,
    )
    merged = await db.merge(obj)
    await db.commit()
    await db.refresh(merged)
    return merged


async def delete_config(db: AsyncSession, tenant_id: str) -> None:
    obj = await get_config(db, tenant_id)
    if obj is not None:
        await db.delete(obj)
        await db.commit()


# ---------- 解密取用（T-M2 解析时调用） ----------

def decrypt_api_key(blob) -> str:
    return vc.decrypt(blob)


def decrypt_embed_api_key(blob) -> Optional[str]:
    return vc.decrypt(blob) if blob else None
