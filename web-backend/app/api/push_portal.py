"""第三方 portal 管理面：自助注册/登录 + 名下应用/token/主题 + 只读用量。

与数据面（push_open.py）鉴权完全两路：这里认 portal 会话令牌
（独立密钥+受众，security.decode_portal_token），pth- 推送到这里一律 401，
反之亦然——身份域隔离的可执行边界。

角色闭包（require_roles）：
  developer：应用 CRUD、token 签发/轮换/吊销、主题 CRUD、消息查询
  operator ：主题 CRUD、消息查询/重试
  viewer   ：消息/统计只读
数据墙：一切 app 维度操作先过 resolve_owned_app（越权 404，不暴露存在性）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.push_hub import PushAccountRole, PushApp, PushDeliveryLog, PushMessage, PushToken, PushTopic
from app.services.push_hub import portal_auth as pa
from app.services.push_hub import tokens as tok_svc

router = APIRouter()


# ---------------------------------------------------------------- 注册 / 登录
class RegisterIn(BaseModel):
    email: str
    password: str
    org_name: str
    phone: Optional[str] = None
    contact: Optional[str] = None


@router.post("/register")
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db)):
    """自助注册（限频由网关对本前缀整体负责）。返回账号状态=pending。"""
    acc = await pa.register(
        db, email=body.email, password=body.password, org_name=body.org_name,
        phone=body.phone, contact=body.contact,
    )
    return {"account_id": acc.account_id, "status": acc.status,
            "message": "注册成功，等待平台管理员审批后生效"}


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)):
    token = await pa.login(db, body.email, body.password)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer"))):
    acc, roles = ctx
    return {
        "account_id": acc.account_id, "email": acc.email, "org_name": acc.org_name,
        "status": acc.status, "roles": roles,
        "approved_at": acc.approved_at.isoformat() if acc.approved_at else None,
    }


# ---------------------------------------------------------------- 应用（developer）
class AppIn(BaseModel):
    name: str = Field(..., max_length=128)
    allowed_channels: list[str] = Field(default_factory=lambda: ["app_offline"])
    rate_limit_per_min: int = Field(60, ge=1, le=10000)


@router.post("/apps")
async def create_app(
    body: AppIn,
    ctx: tuple = Depends(pa.require_roles("developer")),
    db: AsyncSession = Depends(get_db),
):
    acc, _ = ctx
    _validate_channels(body.allowed_channels)
    app = PushApp(
        owner_account_id=acc.account_id, name=body.name,
        allowed_channels=body.allowed_channels, rate_limit_per_min=body.rate_limit_per_min,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return {"app_id": app.app_id, "name": app.name, "owner_account_id": app.owner_account_id}


@router.get("/apps")
async def list_apps(
    ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer")),
    db: AsyncSession = Depends(get_db),
):
    acc, _ = ctx
    rows = (await db.execute(select(PushApp).where(PushApp.owner_account_id == acc.account_id))).scalars().all()
    return [{"app_id": a.app_id, "name": a.name, "enabled": a.enabled,
             "allowed_channels": a.allowed_channels, "rate_limit_per_min": a.rate_limit_per_min}
            for a in rows]


@router.put("/apps/{app_id}")
async def update_app(
    app_id: int, body: AppIn,
    ctx: tuple = Depends(pa.require_roles("developer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    _validate_channels(body.allowed_channels)
    app.name, app.allowed_channels, app.rate_limit_per_min = (
        body.name, body.allowed_channels, body.rate_limit_per_min)
    await db.commit()
    return {"app_id": app.app_id}


@router.delete("/apps/{app_id}")
async def disable_app(
    app_id: int,
    ctx: tuple = Depends(pa.require_roles("developer")),
    db: AsyncSession = Depends(get_db),
):
    """禁用而非硬删：消息/留痕要可追溯（数据治理惯例同 quarantine）。"""
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    app.enabled = False
    await db.commit()
    return {"app_id": app.app_id, "enabled": False}


# ---------------------------------------------------------------- token（developer）
class TokenIn(BaseModel):
    tier: int = Field(..., description="有效期档位（月）：1/3/6/12")
    rate_limit_per_min: Optional[int] = Field(None, ge=1, le=10000)


@router.post("/apps/{app_id}/tokens")
async def issue_token(
    app_id: int, body: TokenIn,
    ctx: tuple = Depends(pa.require_roles("developer")),
    db: AsyncSession = Depends(get_db),
):
    """签发=即换发：旧 active token 自动进 7 天宽限。明文只回显这一次。"""
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    if not app.enabled:
        raise HTTPException(403, "应用已禁用，请先启用")
    try:
        row, raw = await tok_svc.issue_token(
            db, app_id=app.app_id, tier=body.tier,
            created_by=str(acc.account_id), rate_limit_per_min=body.rate_limit_per_min,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {
        "token": raw,  # ⚠️ 仅此一次
        "token_id": row.token_id,
        "token_prefix": row.token_prefix,
        "tier": row.tier,
        "expired_at": row.expired_at.isoformat(),
        "note": "旧 token 进入 7 天宽限期；请在新旧并行窗口内完成替换",
    }


@router.get("/apps/{app_id}/tokens")
async def list_tokens(
    app_id: int,
    ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    rows = (
        await db.execute(select(PushToken).where(PushToken.app_id == app.app_id).order_by(PushToken.token_id))
    ).scalars().all()
    return [
        {
            "token_id": t.token_id, "token_prefix": t.token_prefix, "tier": t.tier,
            "status": t.status, "issued_at": t.issued_at.isoformat() if t.issued_at else None,
            "expired_at": t.expired_at.isoformat() if t.expired_at else None,
            "grace_until": t.grace_until.isoformat() if t.grace_until else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        }
        for t in rows
    ]


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(
    token_id: int,
    ctx: tuple = Depends(pa.require_roles("developer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    t = (await db.execute(select(PushToken).where(PushToken.token_id == token_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "token 不存在")
    await pa.resolve_owned_app(db, acc, roles, t.app_id)  # 越权 404
    ok = await tok_svc.revoke(db, token_id)
    return {"revoked": ok}


# ---------------------------------------------------------------- 主题（developer/operator）
class TopicIn(BaseModel):
    topic_code: str = Field(..., max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(..., max_length=128)
    default_channels: list[str] = Field(default_factory=lambda: ["app_offline"])
    default_ttl: Optional[int] = Field(None, ge=1, le=2592000)
    rate_limit_per_day: int = Field(10000, ge=0, le=1000000)
    force_channels: list[str] = Field(default_factory=list)


@router.post("/apps/{app_id}/topics")
async def create_topic(
    app_id: int, body: TopicIn,
    ctx: tuple = Depends(pa.require_roles("developer", "operator")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    _validate_channels(body.default_channels)
    dup = (
        await db.execute(
            select(PushTopic).where(PushTopic.app_id == app.app_id, PushTopic.topic_code == body.topic_code)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(409, f"主题 {body.topic_code} 已存在")
    t = PushTopic(
        app_id=app.app_id, topic_code=body.topic_code, name=body.name,
        default_channels=body.default_channels, default_ttl=body.default_ttl,
        rate_limit_per_day=body.rate_limit_per_day, force_channels=body.force_channels,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {"topic_id": t.topic_id, "topic_code": t.topic_code}


@router.get("/apps/{app_id}/topics")
async def list_topics(
    app_id: int,
    ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    rows = (
        await db.execute(select(PushTopic).where(PushTopic.app_id == app.app_id).order_by(PushTopic.topic_code))
    ).scalars().all()
    return [
        {"topic_code": t.topic_code, "name": t.name, "enabled": t.enabled,
         "default_channels": t.default_channels, "default_ttl": t.default_ttl,
         "rate_limit_per_day": t.rate_limit_per_day, "force_channels": t.force_channels}
        for t in rows
    ]


@router.put("/topics/{topic_id}")
async def update_topic(
    topic_id: int, body: TopicIn,
    ctx: tuple = Depends(pa.require_roles("developer", "operator")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    t = (await db.execute(select(PushTopic).where(PushTopic.topic_id == topic_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "主题不存在")
    await pa.resolve_owned_app(db, acc, roles, t.app_id)
    _validate_channels(body.default_channels)
    (t.name, t.default_channels, t.default_ttl,
     t.rate_limit_per_day, t.force_channels) = (
        body.name, body.default_channels, body.default_ttl,
        body.rate_limit_per_day, body.force_channels)
    await db.commit()
    return {"topic_id": t.topic_id}


@router.delete("/topics/{topic_id}")
async def disable_topic(
    topic_id: int,
    ctx: tuple = Depends(pa.require_roles("developer", "operator")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    t = (await db.execute(select(PushTopic).where(PushTopic.topic_id == topic_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "主题不存在")
    await pa.resolve_owned_app(db, acc, roles, t.app_id)
    t.enabled = False
    await db.commit()
    return {"topic_id": t.topic_id, "enabled": False}


# ---------------------------------------------------------------- 消息只读（三档角色梯度）
@router.get("/apps/{app_id}/messages")
async def list_messages(
    app_id: int,
    topic_code: Optional[str] = Query(None),
    receiver: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    stmt = select(PushMessage).where(PushMessage.app_id == app.app_id)
    if topic_code:
        stmt = stmt.where(PushMessage.topic_code == topic_code)
    if receiver:
        stmt = stmt.where(PushMessage.receiver == receiver)
    if status:
        stmt = stmt.where(PushMessage.status == status)
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(stmt.order_by(PushMessage.created_at.desc()).offset((page - 1) * size).limit(size))
    ).scalars().all()
    return {
        "total": total, "page": page, "size": size,
        "items": [
            {"msg_id": m.msg_id, "topic_code": m.topic_code, "receiver": m.receiver,
             "title": m.title, "status": m.status,
             "expire_at": m.expire_at.isoformat() if m.expire_at else None,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in rows
        ],
    }


@router.get("/apps/{app_id}/stats")
async def app_stats(
    app_id: int,
    days: int = Query(7, ge=1, le=90),
    ctx: tuple = Depends(pa.require_roles("developer", "operator", "viewer")),
    db: AsyncSession = Depends(get_db),
):
    acc, roles = ctx
    app = await pa.resolve_owned_app(db, acc, roles, app_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    st = dict(
        (
            await db.execute(
                select(PushMessage.status, func.count())
                .where(PushMessage.app_id == app.app_id, PushMessage.created_at >= since)
                .group_by(PushMessage.status)
            )
        ).all()
    )
    by_topic = dict(
        (
            await db.execute(
                select(PushMessage.topic_code, func.count())
                .where(PushMessage.app_id == app.app_id, PushMessage.created_at >= since)
                .group_by(PushMessage.topic_code)
            )
        ).all()
    )
    return {"window_days": days, "status_counts": st, "topic_counts": by_topic}


# 角色授予仅 admin 能做（portal 自助不扩权）——放 admin 路由；此处仅保留查询给已授角色判定用
async def account_roles(db: AsyncSession, account_id: int) -> list[PushAccountRole]:
    return (
        await db.execute(select(PushAccountRole).where(PushAccountRole.account_id == account_id))
    ).scalars().all()


def _validate_channels(chs: list[str]) -> None:
    from app.services.push_hub.routing import VALID_CHANNELS

    if not chs or any(c not in VALID_CHANNELS for c in chs):
        raise HTTPException(422, f"渠道只支持 {VALID_CHANNELS}（vendor/webhook 未开放）")
