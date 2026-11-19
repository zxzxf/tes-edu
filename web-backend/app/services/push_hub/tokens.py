"""推送 token 生命周期：签发 / 校验 / 续期换发 / 吊销。

规则（计划书 v1.3 定稿）：
- 档位 tier ∈ {1,3,6,12} 月，不支持永不过期；
- 过期即拒（Redis 缓存 ``push:tok:{hash}`` TTL 对齐 expired_at，免扫表过期）；
- **续期=换发**：签发新 token 时把同应用旧 active token 置 rotated 并记
  grace_until=now+7d，宽限期内旧新并行可用，之后立即失效；
- 吊销即时 401；应用禁用 / 账号 suspended 在**校验时回溯查上层状态**联动失效，
  不依赖批量 UPDATE（避免停号时漏网）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import settings
from app.models.push_hub import PushAccount, PushApp, PushToken
from app.services.push_hub import security

TIERS_MONTHS = (1, 3, 6, 12)

# 校验结果码（路由层据此回 401/403 + code）
OK = "ok"
INVALID = "TOKEN_INVALID"     # 不存在/已吊销/已轮换超宽限/应用禁用/账号停
EXPIRED = "TOKEN_EXPIRED"     # 自然过期（响应带 expired_at 供接入方自查）


def _redis_key(token_hash: str) -> str:
    return f"push:tok:{token_hash}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """sqlite 读出的 naive datetime 统一补 UTC（PG 侧 timezone=True 本就是 aware）。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def issue_token(
    db: AsyncSession,
    app_id: int,
    tier: int,
    created_by: str,
    rate_limit_per_min: Optional[int] = None,
) -> tuple[PushToken, str]:
    """签发新 token 并执行「续期=换发」：同应用旧 active 行进入 7 天宽限。

    返回 (新 token 行, 明文)。明文只在这里 return 一次，别处不可再取。
    """
    if tier not in TIERS_MONTHS:
        raise ValueError(f"无效档位 {tier}，只支持 {TIERS_MONTHS}")

    now = _now()
    # 被替换的旧 active token：标 rotated + grace_until（并行窗口）
    olds = (
        await db.execute(
            select(PushToken).where(
                PushToken.app_id == app_id, PushToken.status == "active"
            )
        )
    ).scalars().all()
    grace = now + timedelta(days=settings.push_token_grace_days)
    for t in olds:
        t.status = "rotated"
        t.grace_until = grace
        await cache.delete(_redis_key(t.token_hash))

    raw, token_hash, prefix = security.generate_push_token()
    row = PushToken(
        app_id=app_id,
        token_hash=token_hash,
        token_prefix=prefix,
        tier=tier,
        issued_at=now,
        expired_at=now + timedelta(days=30 * tier),
        status="active",
        rate_limit_per_min=rate_limit_per_min,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw


async def revoke(db: AsyncSession, token_id: int) -> bool:
    t = (await db.execute(select(PushToken).where(PushToken.token_id == token_id))).scalar_one_or_none()
    if t is None or t.status == "revoked":
        return False
    t.status = "revoked"
    await db.commit()
    await cache.delete(_redis_key(t.token_hash))
    return True


async def verify_push_token(db: AsyncSession, raw: str) -> tuple[str, Optional[PushToken], Optional[dict]]:
    """校验 Bearer pth- 明文。

    返回 (结果码, token 行|None, 附加信息|None)。
    码=OK 时 token 行带 app_id/tier/rate_limit 与所属应用（调用方按需再取）。
    校验链：格式 → 哈希查 Redis/DB → 状态/过期 → 应用 enabled → 账号 active（联动失效）。
    """
    if not raw or not raw.startswith("pth-") or len(raw) != 36:
        return INVALID, None, None
    token_hash = security.hash_push_token(raw)

    # Redis 快路径：命中即有效（TTL 已对齐 expired_at，过期自动消失）
    cached = await cache.get_json(_redis_key(token_hash))
    t: Optional[PushToken] = None
    if cached and cached.get("status") == OK:
        t = (
            await db.execute(select(PushToken).where(PushToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if t is None:  # 缓存说有效但库里没了（吊销/换发）——以库为准
            await cache.delete(_redis_key(token_hash))
            cached = None
    if cached is None:
        t = (
            await db.execute(select(PushToken).where(PushToken.token_hash == token_hash))
        ).scalar_one_or_none()
        if t is None:
            return INVALID, None, None
        code, info = _check_state(t, _now())
        ttl = _cache_ttl(t, _now())
        if code == OK and ttl > 0:
            await cache.set_json(
                _redis_key(token_hash),
                {"status": OK, "token_id": t.token_id, "app_id": t.app_id},
                ttl,
            )
        elif code != OK:
            await cache.delete(_redis_key(token_hash))
            return code, t, info

    # 走缓存快路径的行也做上层联动检查（app enabled / account active）
    app = (
        await db.execute(select(PushApp).where(PushApp.app_id == t.app_id))
    ).scalar_one_or_none()
    if app is None or not app.enabled:
        return INVALID, t, {"reason": "app_disabled"}
    if app.owner_account_id is not None:
        acc = (
            await db.execute(
                select(PushAccount).where(PushAccount.account_id == app.owner_account_id)
            )
        ).scalar_one_or_none()
        if acc is None or acc.status != "active":
            return INVALID, t, {"reason": "account_not_active"}

    code, info = _check_state(t, _now())
    if code != OK:
        return code, t, info

    t.last_used_at = _now()
    await db.commit()
    return OK, t, {"app": app}


def _check_state(t: PushToken, now: datetime) -> tuple[str, Optional[dict]]:
    expired = _as_aware(t.expired_at)
    if t.status == "active":
        if expired and now >= expired:
            return EXPIRED, {"expired_at": expired.isoformat()}
        return OK, None
    if t.status == "rotated":
        grace = _as_aware(t.grace_until)
        if grace and now < grace and (expired is None or now < expired):
            return OK, None  # 宽限期内并行可用
        return INVALID, {"reason": "rotated_grace_over"}
    return INVALID, {"reason": t.status}  # revoked / 其它


def _cache_ttl(t: PushToken, now: datetime) -> int:
    expired = _as_aware(t.expired_at)
    if expired is None:
        return 60
    return max(0, int((expired - now).total_seconds()))
