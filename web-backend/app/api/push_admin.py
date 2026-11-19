"""推送中心管理面（主站 admin，网关注入头鉴权，走 /api/admin/push/*）。

职责与 portal 正交：
- 第三方账号审批（pending→active/reject、suspended 停启）与角色授予；
- 平台自有应用/主题/token 管理（owner_account_id=NULL，admin 代管）；
- 渠道配置（wechat_oa 的 PushPlus token Fernet 加密入库、有效期、测试发送）；
- 全局运行视图：**只给统计不给第三方消息正文**（隐私口径 v1.4）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.push_hub import (
    PushAccount,
    PushAccountRole,
    PushApp,
    PushChannel,
    PushDeliveryLog,
    PushMessage,
    PushToken,
    PushTopic,
)
from app.services.push_hub import channels as ch
from app.services.push_hub import portal_auth as pa
from app.services.push_hub import security, tokens as tok_svc

router = APIRouter()


# ---------------------------------------------------------------- 账号审批
@router.get("/accounts")
async def list_accounts(
    status: Optional[str] = Query(None),
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(PushAccount).order_by(PushAccount.account_id.desc())
    if status:
        stmt = stmt.where(PushAccount.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {"account_id": a.account_id, "email": a.email, "org_name": a.org_name,
         "status": a.status, "created_at": a.created_at.isoformat() if a.created_at else None,
         "approved_at": a.approved_at.isoformat() if a.approved_at else None}
        for a in rows
    ]


class ApproveIn(BaseModel):
    action: str = Field(..., pattern="^(approve|reject|suspend|resume)$")
    reason: Optional[str] = Field(None, max_length=512)


@router.post("/accounts/{account_id}/approve")
async def approve(
    account_id: int, body: ApproveIn,
    user: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    acc = (await db.execute(select(PushAccount).where(PushAccount.account_id == account_id))).scalar_one_or_none()
    if acc is None:
        raise HTTPException(404, "账号不存在")
    now = datetime.now(timezone.utc)
    if body.action == "approve":
        acc.status, acc.approved_at, acc.approved_by = "active", now, user["user_id"]
        acc.reject_reason = None
    elif body.action == "reject":
        acc.status, acc.reject_reason = "pending", body.reason or "未通过审批（可重新申请）"
        # 驳回保持 pending：允许补充材料场景直接说明；如永久拒绝用 suspend
        acc.status = "pending"
    elif body.action == "suspend":
        acc.status = "suspended"  # 联动：其应用 token 在 verify 时即时失效
    elif body.action == "resume":
        acc.status = "active"
    await db.commit()
    return {"account_id": acc.account_id, "status": acc.status}


class RoleIn(BaseModel):
    role: str = Field(..., pattern="^(developer|operator|viewer)$")
    scope_app_id: Optional[int] = None


@router.post("/accounts/{account_id}/roles")
async def grant_role(
    account_id: int, body: RoleIn,
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    acc = (await db.execute(select(PushAccount).where(PushAccount.account_id == account_id))).scalar_one_or_none()
    if acc is None:
        raise HTTPException(404, "账号不存在")
    if body.scope_app_id is not None:
        app = (await db.execute(select(PushApp).where(PushApp.app_id == body.scope_app_id))).scalar_one_or_none()
        if app is None or app.owner_account_id != account_id:
            raise HTTPException(422, "scope 应用必须归属该账号")
    dup = (
        await db.execute(
            select(PushAccountRole).where(
                PushAccountRole.account_id == account_id,
                PushAccountRole.role == body.role,
                PushAccountRole.scope_app_id == body.scope_app_id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        return {"granted": False, "reason": "角色已存在"}
    db.add(PushAccountRole(account_id=account_id, role=body.role, scope_app_id=body.scope_app_id))
    await db.commit()
    return {"granted": True}


# ---------------------------------------------------------------- 平台自有应用（admin 代管）
class PlatAppIn(BaseModel):
    name: str = Field(..., max_length=128)
    allowed_channels: list[str] = Field(default_factory=lambda: ["app_offline"])
    rate_limit_per_min: int = Field(120, ge=1, le=10000)


@router.post("/apps")
async def create_platform_app(
    body: PlatAppIn,
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.api.push_portal import _validate_channels

    _validate_channels(body.allowed_channels)
    app = PushApp(owner_account_id=None, name=body.name,
                  allowed_channels=body.allowed_channels,
                  rate_limit_per_min=body.rate_limit_per_min)
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return {"app_id": app.app_id, "owner_account_id": None}


class PlatTopicIn(BaseModel):
    topic_code: str = Field(..., max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(..., max_length=128)
    default_channels: list[str] = Field(default_factory=lambda: ["app_offline"])
    default_ttl: Optional[int] = Field(None, ge=1, le=2592000)
    rate_limit_per_day: int = Field(10000, ge=0, le=1000000)
    force_channels: list[str] = Field(default_factory=list)


@router.post("/apps/{app_id}/topics")
async def create_platform_topic(
    app_id: int, body: PlatTopicIn,
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    """平台自有应用的主题由 admin 维护（第三方 portal 只能动自己名下的）。"""
    from app.api.push_portal import _validate_channels

    app = (await db.execute(select(PushApp).where(PushApp.app_id == app_id))).scalar_one_or_none()
    if app is None or app.owner_account_id is not None:
        raise HTTPException(404, "平台自有应用不存在")
    _validate_channels(body.default_channels)
    dup = (
        await db.execute(
            select(PushTopic).where(PushTopic.app_id == app_id, PushTopic.topic_code == body.topic_code)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(409, "主题已存在")
    t = PushTopic(app_id=app_id, **body.model_dump())
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"topic_id": t.topic_id, "topic_code": t.topic_code}


class PlatTokenIn(BaseModel):
    tier: int
    rate_limit_per_min: Optional[int] = Field(None, ge=1, le=10000)


@router.post("/apps/{app_id}/tokens")
async def issue_platform_token(
    app_id: int, body: PlatTokenIn,
    user: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    """给平台自有应用签发推送 token（测试/演示链路用；生产业务走内部 emit）。"""
    app = (await db.execute(select(PushApp).where(PushApp.app_id == app_id))).scalar_one_or_none()
    if app is None or app.owner_account_id is not None:
        raise HTTPException(404, "平台自有应用不存在")
    try:
        row, raw = await tok_svc.issue_token(
            db, app_id=app_id, tier=body.tier, created_by=user.get("user_id", "admin"),
            rate_limit_per_min=body.rate_limit_per_min,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"token": raw, "token_id": row.token_id, "expired_at": row.expired_at.isoformat()}


# ---------------------------------------------------------------- 渠道配置
class ChannelIn(BaseModel):
    channel_type: str = Field(..., pattern="^(wechat_oa|app_offline)$")
    name: str = Field(..., max_length=128)
    enabled: bool = True
    rate_limit_per_min: int = Field(60, ge=1, le=10000)
    cred_valid_until: Optional[str] = None       # ISO 日期
    # wechat_oa: {"pushplus_token": "..."}；仅在提交时加密落库，**永不回显**
    credentials: Optional[dict] = None


@router.get("/channels")
async def list_channels(
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(select(PushChannel).order_by(PushChannel.channel_id))).scalars().all()
    now = datetime.now(timezone.utc)
    out = []
    for c in rows:
        cred_exp = c.cred_valid_until
        if cred_exp is not None and cred_exp.tzinfo is None:
            cred_exp = cred_exp.replace(tzinfo=timezone.utc)
        out.append({
            "channel_id": c.channel_id, "channel_type": c.channel_type, "name": c.name,
            "enabled": c.enabled, "rate_limit_per_min": c.rate_limit_per_min,
            "has_credentials": bool(c.config),          # 只给布尔，密文/明文都不回显
            "cred_valid_until": cred_exp.isoformat() if cred_exp else None,
            "cred_expiring": bool(cred_exp and cred_exp - now < timedelta(days=7)),
            "cred_expired": bool(cred_exp and cred_exp <= now),
        })
    return out


@router.put("/channels/{channel_type}")
async def upsert_channel(
    channel_type: str, body: ChannelIn,
    user: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.channel_type != channel_type:
        raise HTTPException(422, "path 与 body 渠道类型不一致")
    row = (
        await db.execute(select(PushChannel).where(PushChannel.channel_type == channel_type))
    ).scalar_one_or_none()
    if row is None:
        row = PushChannel(channel_type=channel_type, name=body.name)
        db.add(row)
    row.name, row.enabled, row.rate_limit_per_min = body.name, body.enabled, body.rate_limit_per_min
    if body.cred_valid_until:
        row.cred_valid_until = datetime.fromisoformat(body.cred_valid_until)
    if body.credentials:
        try:
            row.config = {"enc": security.enc_channel_config(json.dumps(body.credentials, ensure_ascii=False))}
        except RuntimeError as e:
            # PUSH_SECRET_KEY 未配置 = 服务端环境缺陷，fail-closed 报 500（宁可不存，绝不存明文）
            raise HTTPException(500, str(e))
    await db.commit()
    await db.refresh(row)
    return {"channel_id": row.channel_id, "updated_by": user.get("user_id")}


class TestSendIn(BaseModel):
    channel_type: str = Field(..., pattern="^(wechat_oa|app_offline)$")
    receiver: str = Field(..., max_length=64)


@router.post("/channels/{channel_type}/test")
async def channel_test(
    channel_type: str, body: TestSendIn,
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    """单渠道测试发送：借平台自有『system』消息形态走真实分发链，验证端到端。"""
    if body.channel_type != channel_type:
        raise HTTPException(422, "path 与 body 渠道类型不一致")
    plat = (
        await db.execute(select(PushApp).where(PushApp.owner_account_id.is_(None), PushApp.enabled.is_(True)).limit(1))
    ).scalar_one_or_none()
    if plat is None:
        raise HTTPException(409, "尚无平台自有应用（先 POST /api/admin/push/apps）")
    topic = (
        await db.execute(
            select(PushTopic).where(PushTopic.app_id == plat.app_id, PushTopic.topic_code == "system_test")
        )
    ).scalar_one_or_none()
    if topic is None:
        topic = PushTopic(app_id=plat.app_id, topic_code="system_test", name="渠道测试",
                          default_channels=[channel_type], rate_limit_per_day=500)
        db.add(topic)
        await db.commit()
        await db.refresh(topic)
    from app.services.push_hub import emit as emit_svc

    msg_id = await emit_svc.emit(
        db, app_id=plat.app_id, topic_code="system_test", receiver=body.receiver,
        title="推送中心渠道测试", body=f"channel={channel_type} 端到端测试消息",
        channel_types=[channel_type],
    )
    return {"msg_id": msg_id, "status": "sent_via_dispatch"}


# ---------------------------------------------------------------- 全局统计（不见正文）
@router.get("/stats")
async def global_stats(
    days: int = Query(7, ge=1, le=90),
    _: dict = Depends(pa.require_admin),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    status_counts = dict(
        (await db.execute(
            select(PushMessage.status, func.count())
            .where(PushMessage.created_at >= since).group_by(PushMessage.status)
        )).all()
    )
    channel_results = {}
    rows = (
        await db.execute(
            select(PushDeliveryLog.channel_type, PushDeliveryLog.result_code, func.count())
            .join(PushMessage, PushMessage.msg_id == PushDeliveryLog.msg_id)
            .where(PushDeliveryLog.sent_at >= since)
            .group_by(PushDeliveryLog.channel_type, PushDeliveryLog.result_code)
        )
    ).all()
    for ct, rc, n in rows:
        channel_results.setdefault(ct, {})[rc] = n
    accounts = dict(
        (await db.execute(
            select(PushAccount.status, func.count()).group_by(PushAccount.status)
        )).all()
    )
    expiring = (
        await db.execute(
            select(func.count(PushToken.token_id)).where(
                PushToken.status == "active",
                PushToken.expired_at <= datetime.now(timezone.utc) + timedelta(days=7),
                PushToken.expired_at > datetime.now(timezone.utc),
            )
        )
    ).scalar_one()
    return {
        "window_days": days,
        "message_status": status_counts,
        "delivery_results": channel_results,
        "accounts": accounts,
        "tokens_expiring_7d": expiring,
    }
