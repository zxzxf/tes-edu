"""T-D3：LLM 用量记录 + 成本估算 + 日预算告警。

三件事，全部**best-effort**（任何一步失败都不得拖垮解题主链路）：

1. **用量**：每次远程 LLM 调用（含失败/降级）落一行 ``llm_usage``
   （model / grade / mode / subject / prompt_tokens / completion_tokens /
   latency_ms / degraded / cache_hit / error_kind / cost），异步写 PG；
   缓存命中也记一行（token 与成本为 0），用于统计"省了多少"。
2. **成本**：按模型单价把 token 折算为元（``estimate_cost``）。单价先取
   ``LLM_MODEL_PRICES`` 的按模型覆盖，未登记用 ``LLM_COST_PER_1K_TOKENS_*``。
3. **预算**：当日累计成本对 ``LLM_DAILY_BUDGET`` 判定 ``ok / warn / over``；
   over 时按 ``LLM_BUDGET_ACTION`` 动作——``alert`` 只记审计日志，
   ``force_simple`` 额外强制本轮走小模型（与 T-D2 的衔接点）。

设计约束（与 T-D1 缓存同款）：

- **纯函数优先**：``estimate_cost`` / ``evaluate_budget`` 无 I/O，便于单测逐档断言。
- **可整体关闭**：``USAGE_ENABLED=false`` 或 ``db_ready()`` 为假时不写库、不查累计
  （单测靠它保证 hermetic——单测环境 DB_DSN 指向 106 生产库）。
- **当日累计走 Redis**：``llm:usage:cost:<YYYY-MM-DD>`` 用 ``INCRBYFLOAT`` 累加，
  避免每次调用都扫表；Redis 不可用则回落 PG ``sum(cost)``。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.core import config

log = logging.getLogger("usage")

#: 当日成本累计的进程内缓存（避免每次调用都查 Redis）：(过期时刻, 成本)
_STATE_TTL = 30.0
_state_cache: dict[str, tuple[float, float]] = {}

#: 后台记录任务的持有集合（防 GC 与关闭时等待）
_pending: set = set()


# ---------------------------------------------------------------- 价格与预算


def _prices() -> dict[str, tuple[float, float]]:
    """按模型单价表（元 / 1k tokens）。解析失败返回空表，走默认价。"""
    raw = getattr(config, "LLM_MODEL_PRICES_JSON", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        out: dict[str, tuple[float, float]] = {}
        for model, p in (data or {}).items():
            out[str(model)] = (float(p.get("in")), float(p.get("out")))
        return out
    except Exception:  # noqa: BLE001 配置写错不得启动失败，回落默认价
        log.warning("LLM_MODEL_PRICES 解析失败，回落默认单价")
        return {}


def estimate_cost(
    model: str | None, prompt_tokens: int = 0, completion_tokens: int = 0
) -> float:
    """按模型单价估算成本（元）。token 缺省/异常一律按 0 计，不抛异常。"""
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    table = _prices()
    if (model or "") in table:
        in_p, out_p = table[model]
    else:
        in_p = getattr(config, "LLM_COST_PER_1K_TOKENS_IN", 0.002) or 0.0
        out_p = getattr(config, "LLM_COST_PER_1K_TOKENS_OUT", 0.006) or 0.0
    cost = (pt / 1000.0) * float(in_p) + (ct / 1000.0) * float(out_p)
    return round(cost, 6)


def evaluate_budget(
    used_cost: float,
    budget: float | None = None,
    warn_ratio: float | None = None,
    action: str | None = None,
) -> dict:
    """纯函数：当日累计成本 vs 日预算，给出状态与动作。

    返回 ``{"budget", "used", "ratio", "state", "action", "force_simple"}``：
    - ``state``：``ok``（< 告警线）/ ``warn``（≥ 告警线）/ ``over``（≥ 预算）
      / ``disabled``（预算 ≤ 0，不设限）
    - ``action``：配置动作原样回传（alert / force_simple / none）
    - ``force_simple``：是否应强制走小模型（仅 over 且 action=force_simple 时为真）
    """
    b = float(config.LLM_DAILY_BUDGET if budget is None else budget)
    wr = float(config.LLM_BUDGET_WARN_RATIO if warn_ratio is None else warn_ratio)
    act = (config.LLM_BUDGET_ACTION if action is None else (action or "")).strip().lower()
    used = float(used_cost or 0.0)

    if b <= 0:
        return {
            "budget": b, "used": used, "ratio": None,
            "state": "disabled", "action": act, "force_simple": False,
        }
    ratio = used / b
    if ratio >= 1.0:
        state = "over"
    elif ratio >= wr:
        state = "warn"
    else:
        state = "ok"
    return {
        "budget": b,
        "used": round(used, 6),
        "ratio": round(ratio, 4),
        "state": state,
        "action": act,
        "force_simple": bool(state == "over" and act == "force_simple"),
    }


def day_key(now: datetime | None = None) -> str:
    """当日累计用的日期键（UTC 日期，与 PG 侧 ``created_at::date`` 对齐需注意时区）。"""
    return (now or datetime.now(timezone.utc)).date().isoformat()


# ---------------------------------------------------------------- 当日累计


async def _redis_incr(cost: float, key: str) -> None:
    """当日成本累加到 Redis（best-effort）。"""
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            await r.incrbyfloat(f"llm:usage:cost:{key}", float(cost))
            await r.expire(f"llm:usage:cost:{key}", int(timedelta(days=3).total_seconds()))
        finally:
            await r.aclose()
    except Exception as e:  # noqa: BLE001 Redis 抖动不得影响主链路
        log.debug("usage redis incr failed: %s", e)


async def _redis_get(key: str) -> float | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            v = await r.get(f"llm:usage:cost:{key}")
            return float(v) if v is not None else 0.0
        finally:
            await r.aclose()
    except Exception as e:  # noqa: BLE001
        log.debug("usage redis get failed: %s", e)
        return None


async def daily_cost(now: datetime | None = None) -> float:
    """当日累计成本（元）。Redis 优先，回落 PG；全失败返回 0.0（不阻塞）。"""
    if not config.usage_enabled():
        return 0.0
    key = day_key(now)

    hit = _state_cache.get(key)
    import time as _t

    if hit and _t.monotonic() - hit[0] < _STATE_TTL:
        return hit[1]

    val = await _redis_get(key)
    if val is None:
        val = await _daily_cost_pg(key)
    elif val <= 0 and config.db_ready():
        # Redis 有 key 但为 0（或刚过期）：以 PG 为准补一次，防低估
        pg = await _daily_cost_pg(key)
        val = max(val, pg)
    _state_cache[key] = (_t.monotonic(), float(val or 0.0))
    return float(val or 0.0)


async def _daily_cost_pg(key: str) -> float:
    try:
        from app.db.pg import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(sum(cost),0)::float AS c FROM llm_usage "
                "WHERE created_at >= $1::date AND created_at < $1::date + interval '1 day'",
                key,
            )
            return float(row["c"]) if row else 0.0
    except Exception as e:  # noqa: BLE001
        log.debug("usage daily sum failed: %s", e)
        return 0.0


async def budget_state(now: datetime | None = None) -> dict:
    """当前预算状态（带 30s 进程内缓存）。用量关闭或预算 ≤0 时恒 ok/disabled。"""
    if not config.usage_enabled():
        return evaluate_budget(0.0)
    return evaluate_budget(await daily_cost(now))


# ---------------------------------------------------------------- 用量落库


def build_usage_row(
    *,
    model: str | None = None,
    mode: str | None = None,
    grade: str | None = None,
    subject: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    degraded: bool = False,
    cache_hit: bool = False,
    error_kind: str | None = None,
    cost: float | None = None,
) -> dict:
    """组装一行用量记录（纯函数，便于单测断言字段与成本口径）。"""
    amount = estimate_cost(model, prompt_tokens, completion_tokens) if cost is None else float(cost)
    if cache_hit:
        amount = 0.0
    return {
        "model": model or "",
        "grade": grade or "",
        "mode": mode or "",
        "subject": subject or "",
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "latency_ms": int(latency_ms or 0),
        "degraded": bool(degraded),
        "cache_hit": bool(cache_hit),
        "error_kind": error_kind,
        "cost": round(amount, 6),
    }


async def record_usage(
    *,
    model: str | None = None,
    mode: str | None = None,
    grade: str | None = None,
    subject: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    degraded: bool = False,
    cache_hit: bool = False,
    error_kind: str | None = None,
    cost: float | None = None,
    recorder=None,
) -> dict:
    """记录一次 LLM 调用（best-effort：写失败只告警，绝不抛给调用方）。

    ``error_kind``：``quota``（403 配额）/ ``timeout`` / ``other`` / None（成功）。
    ``cache_hit=True`` 表示命中缓存未真调 LLM（token 与成本按 0）。
    ``recorder``：注入式落库钩子（async callable），单测用它采集而不碰 PG。
    """
    if not config.usage_enabled():
        return {}
    row = build_usage_row(
        model=model, mode=mode, grade=grade, subject=subject,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_ms=latency_ms, degraded=degraded, cache_hit=cache_hit,
        error_kind=error_kind, cost=cost,
    )

    # 当日累计：仅真实调用计入（缓存命中不产生成本）
    if not cache_hit and row["cost"]:
        try:
            await _redis_incr(row["cost"], day_key())
        except Exception:  # noqa: BLE001
            pass

    if recorder is not None:
        try:
            await recorder(row)
        except Exception as e:  # noqa: BLE001
            log.debug("usage recorder failed: %s", e)
        return row

    if not config.db_ready():
        return row
    try:
        from app.db.pg import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO llm_usage
                   (model, grade, mode, subject, prompt_tokens, completion_tokens,
                    latency_ms, degraded, cache_hit, error_kind, cost)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                row["model"], row["grade"], row["mode"], row["subject"],
                row["prompt_tokens"], row["completion_tokens"], row["latency_ms"],
                row["degraded"], row["cache_hit"], row["error_kind"], row["cost"],
            )
    except Exception as e:  # noqa: BLE001 表不存在/库抖动：不拖垮解题链路
        log.warning("usage record failed: %s", e)
    return row


def record_usage_bg(**kwargs) -> None:
    """后台记录用量，不阻塞当前请求（与 events.emit_student_event_bg 同款）。"""
    if not config.usage_enabled():
        return
    try:
        import asyncio

        task = asyncio.create_task(record_usage(**kwargs))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:  # 无事件循环（同步上下文）时退化为不记录
        return


async def wait_pending(timeout: float = 3.0) -> None:
    """等待后台用量任务收尾（关闭时调用）。"""
    import asyncio

    if _pending:
        await asyncio.wait(list(_pending), timeout=timeout)


def classify_error(exc: Exception) -> str:
    """把异常归为用量表的 ``error_kind``：quota / timeout / other。"""
    text = f"{type(exc).__name__}:{exc}".lower()
    if "403" in text or "quota" in text or "rate limit" in text or "429" in text:
        return "quota"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    return "other"


def audit_budget(state: dict) -> None:
    """预算审计日志：warn/over 时打点（结构化前缀便于采集告警）。"""
    if state.get("state") in ("warn", "over"):
        log.warning(
            "[budget-audit] state=%s used=%.4f budget=%.2f ratio=%s action=%s",
            state.get("state"), state.get("used", 0.0), state.get("budget", 0.0),
            state.get("ratio"), state.get("action"),
        )
