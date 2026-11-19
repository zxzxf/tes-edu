"""推送中心数据面 OpenAPI：第三方 App 服务器用 pth- token 调。

路由（挂载前缀由 main.py 给，网关 /api/push/open/）：
  POST /send          发送一条（带 topic_code）
  GET  /topics        本应用已注册主题
  GET  /token-info    当前 token 档位/剩余有效期

鉴权：Authorization: Bearer pth-<32hex>（**非** portal 会话令牌、**非** 主站 JWT）。
verify_push_token 内建上层联动：应用禁用/账号停 → INVALID（§v1.4）。
频控三级：token QPM → 应用 QPM → 主题日额，任一超限 429（保护性，Redis 挂=放行）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.push_hub import PushApp, PushToken, PushTopic
from app.services.push_hub import emit as emit_svc
from app.services.push_hub import ratelimit, tokens as tok_svc

router = APIRouter()

_CODE_401 = {
    tok_svc.INVALID: (401, "token 无效（不存在/已吊销/应用禁用/账号停用）"),
    tok_svc.EXPIRED: (401, "token 已过期，请到 portal 续期换发"),
}


async def _ctx(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    """解析并校验 Bearer pth-，返回 (token 行, 应用)。失败抛 401。"""
    raw = (authorization or "").strip()
    if raw.startswith("Bearer "):
        raw = raw[7:].strip()
    code, tok, info = await tok_svc.verify_push_token(db, raw)
    if code != tok_svc.OK:
        msg = _CODE_401.get(code, (401, "token 校验失败"))[1]
        detail = {"code": code, "message": msg}
        if code == tok_svc.EXPIRED and info:
            detail["expired_at"] = info.get("expired_at")
        raise HTTPException(status_code=401, detail=detail)
    app = info.get("app") if info else None
    if app is None:
        app = (await db.execute(select(PushApp).where(PushApp.app_id == tok.app_id))).scalar_one_or_none()
    return tok, app


class SendIn(BaseModel):
    topic_code: str = Field(..., max_length=64)
    receiver: str = Field(..., max_length=64)
    title: str = Field(..., max_length=128)
    body: str = Field(..., max_length=2000)
    payload: Optional[dict] = None
    channel_types: Optional[list[str]] = None
    ttl_seconds: Optional[int] = Field(None, ge=1, le=2592000)  # ≤30 天


@router.post("/send")
async def send(
    body: SendIn,
    ctx=Depends(_ctx),
    db: AsyncSession = Depends(get_db),
):
    tok, app = ctx
    # 三级频控（token/app 先拦贵的）
    if not await ratelimit.check_token_qpm(tok.token_id, tok.rate_limit_per_min or app.rate_limit_per_min):
        raise HTTPException(429, {"code": "TOKEN_RATE_LIMIT", "message": "超过 token 每分钟限额"})
    if not await ratelimit.check_app_qpm(app.app_id, app.rate_limit_per_min):
        raise HTTPException(429, {"code": "APP_RATE_LIMIT", "message": "超过应用每分钟限额"})

    # 主题日额：发送前拦截（emit 内部会再校验主题存在性，此处取日额配置）
    topic = (
        await db.execute(
            select(PushTopic).where(
                PushTopic.app_id == app.app_id, PushTopic.topic_code == body.topic_code
            )
        )
    ).scalar_one_or_none()
    if topic is not None and not await ratelimit.check_topic_daily(
        app.app_id, body.topic_code, topic.rate_limit_per_day
    ):
        raise HTTPException(429, {"code": "TOPIC_DAILY_LIMIT", "message": f"主题 {body.topic_code} 超过当日限额"})

    try:
        msg_id = await emit_svc.emit(
            db,
            app_id=app.app_id,
            topic_code=body.topic_code,
            receiver=body.receiver,
            title=body.title,
            body=body.body,
            payload=body.payload,
            channel_types=body.channel_types,
            ttl_seconds=body.ttl_seconds,
            token_id=tok.token_id,
        )
    except emit_svc.DuplicateMsg as e:
        return {"msg_id": str(e), "status": "duplicate"}
    except emit_svc.TopicNotFound as e:
        raise HTTPException(422, {"code": "TOPIC_NOT_REGISTERED", "message": str(e)})
    except emit_svc.TopicDisabled as e:
        raise HTTPException(403, {"code": "TOPIC_DISABLED", "message": str(e)})
    except emit_svc.AppDisabled as e:
        raise HTTPException(403, {"code": "APP_DISABLED", "message": str(e)})

    return {"msg_id": msg_id, "status": "accepted"}


@router.get("/topics")
async def list_topics(ctx=Depends(_ctx), db: AsyncSession = Depends(get_db)):
    _, app = ctx
    rows = (
        await db.execute(select(PushTopic).where(PushTopic.app_id == app.app_id).order_by(PushTopic.topic_code))
    ).scalars().all()
    return [
        {
            "topic_code": t.topic_code,
            "name": t.name,
            "enabled": t.enabled,
            "default_channels": t.default_channels,
        }
        for t in rows
    ]


@router.get("/token-info")
async def token_info(ctx=Depends(_ctx)):
    tok, app = ctx
    now = datetime.now(timezone.utc)
    exp = tok.expired_at if tok.expired_at.tzinfo else tok.expired_at.replace(tzinfo=timezone.utc)
    return {
        "token_prefix": tok.token_prefix,
        "tier": tok.tier,
        "status": tok.status,
        "app": app.name,
        "expired_at": exp.isoformat(),
        "days_left": max(0, (exp - now).days),
    }
