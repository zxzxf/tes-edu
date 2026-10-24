"""T-D3：LLM 用量聚合与预算告警（B 端只读）。

数据来源 ``llm_usage``（agent-backend 每次远程调用落一行，缓存命中也落一行）。
设计为「纯函数聚合 + DB 薄层」，与 T-C4 的 p2_metrics 同款，便于单测用
构造行直接喂纯函数。

聚合口径：

- ``calls``        总行数（含缓存命中）
- ``real_calls``   真正调用 LLM 的行数（cache_hit=false）
- ``cache_hit_rate`` 缓存命中率 = cache_hits / calls
- ``degraded_rate`` 降级率 = degraded / real_calls（缓存命中不可能是降级）
- ``cost_total``   估算成本合计（元），按模型/按日同样聚合
- ``error_breakdown`` 失败原因分布（quota / timeout / other）
- ``budget``       当日成本对日预算的判定（与 agent 侧 evaluate_budget 同口径）

预算配置走环境变量（与 agent 一致）：``LLM_DAILY_BUDGET`` / ``LLM_BUDGET_WARN_RATIO``
/ ``LLM_BUDGET_ACTION``；B 端只做判定展示，不执行动作。
"""
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import LlmUsage


def _g(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        v = row.get(key, None)
    else:
        v = getattr(row, key, None)
    return v if v is not None else default


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _dt(row: Any) -> Optional[datetime]:
    return _g(row, "created_at") or _g(row, "occurred_at")


def _day(row: Any) -> str:
    d = _dt(row)
    return (d.date().isoformat() if d else "") or ""


# ---------------------------------------------------------------------------
# 预算判定（与 agent app.core.usage.evaluate_budget 同口径，纯函数）
# ---------------------------------------------------------------------------
def budget_config() -> Dict[str, Any]:
    return {
        "budget": float(os.getenv("LLM_DAILY_BUDGET", "50") or 0),
        "warn_ratio": float(os.getenv("LLM_BUDGET_WARN_RATIO", "0.8") or 0),
        "action": (os.getenv("LLM_BUDGET_ACTION", "alert") or "alert").strip().lower(),
    }


def evaluate_budget(
    used_cost: float,
    budget: Optional[float] = None,
    warn_ratio: Optional[float] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    """当日累计成本 vs 日预算：ok / warn / over / disabled。"""
    cfg = budget_config()
    b = float(cfg["budget"] if budget is None else budget)
    wr = float(cfg["warn_ratio"] if warn_ratio is None else warn_ratio)
    act = (cfg["action"] if action is None else (action or "")).strip().lower()
    used = float(used_cost or 0.0)

    if b <= 0:
        return {
            "budget": b, "used": round(used, 6), "ratio": None,
            "state": "disabled", "action": act, "force_simple": False,
        }
    ratio = used / b
    state = "over" if ratio >= 1.0 else ("warn" if ratio >= wr else "ok")
    return {
        "budget": b,
        "used": round(used, 6),
        "ratio": round(ratio, 4),
        "state": state,
        "action": act,
        "force_simple": bool(state == "over" and act == "force_simple"),
    }


# ---------------------------------------------------------------------------
# 用量聚合（纯函数）
# ---------------------------------------------------------------------------
def _empty_bucket() -> Dict[str, Any]:
    return {
        "calls": 0, "real_calls": 0, "cache_hits": 0, "degraded": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0,
        "cost": 0.0, "errors": {"quota": 0, "timeout": 0, "other": 0},
    }


def _accumulate(bucket: Dict[str, Any], row: Any) -> None:
    cache_hit = bool(_g(row, "cache_hit", False))
    bucket["calls"] += 1
    if cache_hit:
        # 缓存命中：不消耗 token、不产生延迟、成本为 0，只计入命中次数
        bucket["cache_hits"] += 1
        return
    bucket["prompt_tokens"] += _i(_g(row, "prompt_tokens"))
    bucket["completion_tokens"] += _i(_g(row, "completion_tokens"))
    bucket["latency_ms"] += _i(_g(row, "latency_ms"))
    bucket["cost"] += _f(_g(row, "cost"))
    bucket["real_calls"] += 1
    if bool(_g(row, "degraded", False)):
        bucket["degraded"] += 1
    kind = _g(row, "error_kind")
    if kind:
        bucket["errors"][str(kind) if str(kind) in bucket["errors"] else "other"] += 1


def _finalize(bucket: Dict[str, Any]) -> Dict[str, Any]:
    calls = bucket["calls"]
    real = bucket["real_calls"]
    out = dict(bucket)
    out["cost"] = round(bucket["cost"], 6)
    out["cache_hit_rate"] = round(bucket["cache_hits"] / calls, 4) if calls else None
    out["degraded_rate"] = round(bucket["degraded"] / real, 4) if real else None
    out["avg_latency_ms"] = round(bucket["latency_ms"] / real, 1) if real else None
    out["errors"] = dict(bucket["errors"])
    return out


def compute_llm_usage(rows: Iterable[Any]) -> Dict[str, Any]:
    """纯函数：输入 llm_usage 行，输出总览 + 按模型/按日分组 + 预算状态。"""
    rows = list(rows)
    total = _empty_bucket()
    by_model: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        _accumulate(total, row)
        model = str(_g(row, "model") or "")
        day = _day(row)
        for key, store in ((model, by_model), (day, by_day)):
            if key:
                store.setdefault(key, _empty_bucket())
                _accumulate(store[key], row)

    # 当日成本（按本地"最大日期"取——B 端主要关心最近一天）
    today_cost = 0.0
    if by_day:
        last_day = max(by_day)
        today_cost = by_day[last_day]["cost"]

    return {
        "total": _finalize(total),
        "by_model": {k: _finalize(v) for k, v in sorted(by_model.items())},
        "by_day": {k: _finalize(v) for k, v in sorted(by_day.items())},
        "budget": evaluate_budget(today_cost),
        "_counts": {"rows": len(rows)},
    }


# ---------------------------------------------------------------------------
# DB 薄层
# ---------------------------------------------------------------------------
async def compute_llm_usage_for(
    db: AsyncSession,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """查 llm_usage 并聚合（B 端只读）。"""
    stmt = select(LlmUsage)
    if from_dt:
        stmt = stmt.where(LlmUsage.created_at >= from_dt)
    if to_dt:
        stmt = stmt.where(LlmUsage.created_at <= to_dt)
    if model:
        stmt = stmt.where(LlmUsage.model == model)
    res = await db.execute(stmt)
    return compute_llm_usage(list(res.scalars().all()))


def degraded_rate(rows: Iterable[Any]) -> Optional[float]:
    """便捷口径：真实调用中的降级占比（无真实调用返回 None）。"""
    rows = list(rows)
    real = [r for r in rows if not _g(r, "cache_hit", False)]
    if not real:
        return None
    deg = sum(1 for r in real if _g(r, "degraded", False))
    return round(deg / len(real), 4)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
