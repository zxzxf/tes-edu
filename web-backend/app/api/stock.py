"""股票盯盘业务中心 API（v1.4 计划书阶段 2/3 的接口面）。

鉴权：网关 /api/stock/ rbac + 本文件 require_role(ADMIN) 双闸门——仅 admin。
路径全挂 /api/stock/*（网关转 web-backend），前端 api/stock.js 对应封装。

读路径降级铁律（§七"数据源抖动"）：报价读 Redis 快照，miss 现拉源；
两者全挂返回空列表 + source_status=degraded + last_fetched_at，绝不 500。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.stock_models import (
    StockAlert,
    StockAlertLog,
    StockConfig,
    StockFriend,
    StockWatchGroup,
    StockWatchlist,
)
from app.services.stock import engine, provider
from app.services.stock import cache as stock_cache

logger = logging.getLogger("stock.api")
router = APIRouter(tags=["stock"])  # 挂 /api/stock 由 main.py include prefix 决定（router 不叠前缀）

AdminDep = Depends(require_role(Role.ADMIN))

_TOKEN_RE = re.compile(r"[0-9a-f]{32}")


# ---------------------------------------------------------------- 入参模型
class WatchIn(BaseModel):
    symbol: str
    name: str = ""
    group_id: Optional[int] = None


class WatchPatchIn(BaseModel):
    name: Optional[str] = None
    group_id: Optional[int] = None
    pinned: Optional[bool] = None


class GroupIn(BaseModel):
    name: str = Field(..., max_length=64)


class AlertIn(BaseModel):
    symbol: str
    condition: str
    threshold: float
    push_enabled: bool = True
    receiver_tokens: list[str] = []
    cooldown_minutes: int = 30
    enabled: bool = True


class AlertPatchIn(BaseModel):
    condition: Optional[str] = None
    threshold: Optional[float] = None
    push_enabled: Optional[bool] = None
    receiver_tokens: Optional[list[str]] = None
    cooldown_minutes: Optional[int] = None
    enabled: Optional[bool] = None


class FriendIn(BaseModel):
    alias: str = Field(..., max_length=64)
    token: str
    note: str = ""


class PushDefaultIn(BaseModel):
    mode: str = "self"  # self | friends
    tokens: list[str] = []


class PushTestIn(BaseModel):
    receiver: str = "SELF"  # SELF 或好友令牌


def _valid_symbol_or_422(raw: str) -> str:
    sym = provider.normalize_symbol(raw)
    if not sym:
        raise HTTPException(422, f"symbol 不合法：{raw}（A 股 6 位数字或 sh/sz/bj 前缀）")
    return sym


def _valid_token_or_422(raw: str) -> str:
    t = (raw or "").strip().lower()
    if not _TOKEN_RE.fullmatch(t):
        raise HTTPException(422, "好友令牌须为 PushPlus 的 32 位十六进制串")
    return t


def _alert_dict(a: StockAlert) -> dict:
    return {
        "alert_id": a.alert_id, "symbol": a.symbol, "name": a.name,
        "condition": a.condition, "threshold": float(a.threshold or 0),
        "push_enabled": a.push_enabled, "receiver_tokens": a.receiver_tokens or [],
        "cooldown_minutes": a.cooldown_minutes, "enabled": a.enabled,
    }


# ---------------------------------------------------------------- 行情读取
@router.get("/quotes")
async def quotes(
    symbols: Optional[str] = Query(None, description="逗号分隔；留空=自选股+指数"),
    force: bool = Query(False, description="true=跳过快照直接拉源"),
    _: dict = AdminDep,
    db: AsyncSession = Depends(get_db),
):
    """报价列表：Redis 快照优先 → miss/force 现拉源；全挂→degraded 空列表。"""
    if symbols:
        syms = [_valid_symbol_or_422(s) for s in symbols.split(",") if s.strip()]
    else:
        watch = (await db.execute(select(StockWatchlist.symbol))).scalars().all()
        syms = provider.INDEX_SYMBOLS + [s for s in watch if s not in provider.INDEX_SYMBOLS]
    if not syms:
        return {"items": [], "source_status": "ok", "last_fetched_at": None}

    out: dict[str, dict] = {} if force else await stock_cache.get_quotes(syms)
    missing = [s for s in syms if s not in out]
    degraded = False
    if missing:
        try:
            live = await provider.fetch_quotes(missing)
        except Exception as e:  # noqa: BLE001（provider 内部已兜，这里双保险）
            logger.warning("现拉源失败：%s", e)
            live = {}
        out.update(live)
        degraded = len(live) < len(missing)
        for s, q in live.items():
            await stock_cache.put_quote(s, q)
    last = await stock_cache.get_refresh_mark()
    items = [out[s] for s in syms if s in out]
    return {
        "items": items,
        "source_status": "degraded" if degraded and not items else ("ok" if not degraded else "partial"),
        "last_fetched_at": last,
    }


@router.get("/kline")
async def kline(
    symbol: str = Query(...),
    period: str = Query("day", pattern="^(day|week|month)$"),
    limit: int = Query(120, ge=10, le=500),
    _: dict = AdminDep,
):
    return await provider.fetch_kline(_valid_symbol_or_422(symbol), period, limit)


@router.get("/intraday")
async def intraday(symbol: str = Query(...), _: dict = AdminDep):
    return await provider.fetch_intraday(_valid_symbol_or_422(symbol))


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=32),
    _: dict = AdminDep,
):
    """代码/名称查询：东财 suggest 免注册接口；失败回空列表不 500。"""
    import httpx

    url = ("https://searchadapter.eastmoney.com/api/suggest/get"
           f"?input={q}&type=14&count=10")
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(url)
            data = resp.json()
    except Exception:  # noqa: BLE001
        return {"items": []}
    items = []
    for it in ((data or {}).get("QuotationCodeTable") or {}).get("Data") or []:
        code, market_type = it.get("Code") or "", it.get("SecurityTypeName") or ""
        mkt = {"上海": "sh", "深圳": "sz", "北京": "bj"}.get(market_type, "")
        sym = provider.normalize_symbol(f"{mkt}{code}") if mkt else None
        if sym:
            items.append({"symbol": sym, "name": it.get("Name") or code})
    return {"items": items[:10]}


# ---------------------------------------------------------------- 自选股
@router.get("/watchlist")
async def list_watch(_: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    groups = (await db.execute(select(StockWatchGroup).order_by(StockWatchGroup.sort))).scalars().all()
    rows = (
        await db.execute(select(StockWatchlist).order_by(
            StockWatchlist.pinned.desc(), StockWatchlist.sort, StockWatchlist.id))
    ).scalars().all()
    return {
        "groups": [{"group_id": g.group_id, "name": g.name} for g in groups],
        "items": [{
            "id": w.id, "symbol": w.symbol, "name": w.name,
            "group_id": w.group_id, "pinned": w.pinned, "sort": w.sort,
        } for w in rows],
    }


@router.post("/watchlist", status_code=201)
async def add_watch(body: WatchIn, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    sym = _valid_symbol_or_422(body.symbol)
    dup = (await db.execute(select(StockWatchlist).where(StockWatchlist.symbol == sym))).scalars().first()
    if dup:
        raise HTTPException(409, "该 symbol 已在自选股")
    name = body.name
    if not name:
        q = await provider.fetch_quotes([sym])
        name = (q.get(sym) or {}).get("name") or sym
    row = StockWatchlist(symbol=sym, name=name[:64], group_id=body.group_id)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "symbol": row.symbol, "name": row.name}


@router.patch("/watchlist/{row_id}")
async def patch_watch(row_id: int, body: WatchPatchIn, _: dict = AdminDep,
                      db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(StockWatchlist).where(StockWatchlist.id == row_id))).scalars().first()
    if row is None:
        raise HTTPException(404, "自选股不存在")
    if body.name is not None:
        row.name = body.name[:64]
    if body.group_id is not None:
        row.group_id = body.group_id or None
    if body.pinned is not None:
        row.pinned = body.pinned
    await db.commit()
    return {"id": row.id, "pinned": row.pinned, "group_id": row.group_id}


@router.delete("/watchlist/{row_id}")
async def del_watch(row_id: int, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(StockWatchlist).where(StockWatchlist.id == row_id))
    await db.commit()
    return {"deleted": row_id}


@router.post("/watch-groups", status_code=201)
async def add_group(body: GroupIn, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    dup = (await db.execute(select(StockWatchGroup).where(StockWatchGroup.name == body.name))).scalars().first()
    if dup:
        raise HTTPException(409, "分组同名")
    row = StockWatchGroup(name=body.name)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"group_id": row.group_id, "name": row.name}


@router.delete("/watch-groups/{group_id}")
async def del_group(group_id: int, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(StockWatchGroup).where(StockWatchGroup.group_id == group_id))).scalars().first()
    if row is None:
        raise HTTPException(404, "分组不存在")
    # 组内自选不删，仅脱组
    rows = (await db.execute(select(StockWatchlist).where(StockWatchlist.group_id == group_id))).scalars().all()
    for w in rows:
        w.group_id = None
    await db.delete(row)
    await db.commit()
    return {"deleted": group_id, "detached": len(rows)}


# ---------------------------------------------------------------- 预警规则
@router.get("/alerts")
async def list_alerts(_: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(StockAlert).order_by(StockAlert.alert_id.desc()))).scalars().all()
    return {"items": [_alert_dict(a) for a in rows]}


@router.post("/alerts", status_code=201)
async def add_alert(body: AlertIn, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    sym = _valid_symbol_or_422(body.symbol)
    if body.condition not in engine.CONDITIONS:
        raise HTTPException(422, f"condition 只支持 {engine.CONDITIONS}")
    tokens = [_valid_token_or_422(t) for t in body.receiver_tokens]
    if len(tokens) > 10:
        raise HTTPException(422, "单条预警接收好友上限 10 个（PushPlus 实名口径）")
    q = await provider.fetch_quotes([sym])
    name = (q.get(sym) or {}).get("name") or sym
    row = StockAlert(
        symbol=sym, name=name[:64], condition=body.condition, threshold=body.threshold,
        push_enabled=body.push_enabled, receiver_tokens=tokens,
        cooldown_minutes=max(0, int(body.cooldown_minutes)), enabled=body.enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _alert_dict(row)


@router.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: int, body: AlertPatchIn, _: dict = AdminDep,
                      db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(StockAlert).where(StockAlert.alert_id == alert_id))).scalars().first()
    if row is None:
        raise HTTPException(404, "预警不存在")
    if body.condition is not None:
        if body.condition not in engine.CONDITIONS:
            raise HTTPException(422, f"condition 只支持 {engine.CONDITIONS}")
        row.condition = body.condition
    if body.threshold is not None:
        row.threshold = body.threshold
    if body.push_enabled is not None:
        row.push_enabled = body.push_enabled
    if body.receiver_tokens is not None:
        row.receiver_tokens = [_valid_token_or_422(t) for t in body.receiver_tokens][:10]
    if body.cooldown_minutes is not None:
        row.cooldown_minutes = max(0, int(body.cooldown_minutes))
    if body.enabled is not None:
        row.enabled = body.enabled
    await db.commit()
    return _alert_dict(row)


@router.delete("/alerts/{alert_id}")
async def del_alert(alert_id: int, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(StockAlert).where(StockAlert.alert_id == alert_id))
    await db.commit()
    return {"deleted": alert_id}


@router.get("/alert-logs")
async def alert_logs(
    alert_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(30, ge=1, le=100),
    _: dict = AdminDep,
    db: AsyncSession = Depends(get_db),
):
    cond = [StockAlertLog.alert_id == alert_id] if alert_id else []
    total = len((await db.execute(
        select(StockAlertLog.id).where(*cond))).all())
    rows = (
        await db.execute(
            select(StockAlertLog).where(*cond)
            .order_by(StockAlertLog.fired_at.desc()).offset((page - 1) * size).limit(size)
        )
    ).scalars().all()
    return {
        "total": total, "page": page, "size": size,
        "items": [{
            "id": l.id, "alert_id": l.alert_id, "symbol": l.symbol,
            "price": float(l.price or 0), "pct": float(l.pct or 0),
            "fired_at": l.fired_at.isoformat() if l.fired_at else None,
            "pushed": l.pushed, "receivers": l.receivers or [],
        } for l in rows],
    }


# ---------------------------------------------------------------- 好友
@router.get("/friends")
async def list_friends(_: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(StockFriend).order_by(StockFriend.friend_id))).scalars().all()
    return {"items": [{
        "friend_id": f.friend_id, "alias": f.alias,
        "token_mask": f.token[:4] + "…" + f.token[-4:], "token": f.token,
        "note": f.note or "",
    } for f in rows]}


@router.post("/friends", status_code=201)
async def add_friend(body: FriendIn, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    token = _valid_token_or_422(body.token)
    dup = (await db.execute(select(StockFriend).where(StockFriend.token == token))).scalars().first()
    if dup:
        raise HTTPException(409, "该令牌已在好友列表")
    row = StockFriend(alias=body.alias, token=token, note=body.note[:255])
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"friend_id": row.friend_id, "alias": row.alias}


@router.delete("/friends/{friend_id}")
async def del_friend(friend_id: int, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(StockFriend).where(StockFriend.friend_id == friend_id))
    await db.commit()
    return {"deleted": friend_id}


# ---------------------------------------------------------------- 推送默认与测试
@router.get("/push-default")
async def get_push_default_api(_: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    return await engine.get_push_default(db)


@router.put("/push-default")
async def put_push_default(body: PushDefaultIn, _: dict = AdminDep,
                           db: AsyncSession = Depends(get_db)):
    if body.mode not in ("self", "friends"):
        raise HTTPException(422, "mode 只支持 self / friends")
    tokens = [_valid_token_or_422(t) for t in body.tokens][:10]
    if body.mode == "friends" and not tokens:
        raise HTTPException(422, "friends 模式至少选一个好友")
    value = {"mode": body.mode, "tokens": tokens}
    row = (await db.execute(select(StockConfig).where(StockConfig.key == "push_default"))).scalars().first()
    if row is None:
        db.add(StockConfig(key="push_default", value=value))
    else:
        row.value = value
    await db.commit()
    return value


@router.post("/push-test")
async def push_test(body: PushTestIn, _: dict = AdminDep, db: AsyncSession = Depends(get_db)):
    """手动发测试消息验证自己/好友可达（走推送中心真实链路，返回 msg_id 供查状态）。"""
    from app.services.stock.push import push_alert

    receiver = (body.receiver or "SELF").strip().lower()
    if receiver != "self":
        receiver = _valid_token_or_422(receiver)
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    results = await push_alert(
        db, "盯盘推送测试",
        f"这是一条来自股票盯盘业务中心的测试消息（{now}）。收到即渠道可达。",
        [receiver],
    )
    return {"results": results}
