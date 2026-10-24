"""T-C4：P2 引导式对话六项埋点指标聚合。

数据全部来自 student_events 流水（含 T-C1 intent / T-C2 Hint 阶梯 / T-C3 题型路由
与直接看解析的埋点字段）。六指标口径与《P2 引导式对话与横向收尾任务计划书》逐字对齐，
所有信号均可由**现有事件**派生，无需改动 agent-backend 的生产发射路径：

  1) 首轮直给率       = 系统在第 1 轮直接给答案的会话占比
                       （分子：首事件为 solve 且 intent=="direct"；
                        分母：不含 direct_reveal 的学生自发会话。R6：主动看解析不计）
  2) 提示有效推进率   = 一次 Hint 后产生"推进"的占比
                       （推进 = 同会话下一事件等级更高，或下一事件为正确 solve；
                        亦兼容显式 hint_progress 事件）
  3) 单题平均交互轮次 = 每会话（学生×题）的交互轮数均值（hint_shown+direct_reveal+solve）
  4) 规避/放弃率      = （绕过引导 + 中途放弃）会话占比
                       （规避 = 看过 direct_reveal 后又提交作答；放弃 = 起了 hint 但既没作答也没看解析就离开）
  5) 挫败信号率       = 带挫败连击(hint_frustration_streak>=2)的 Hint 轮占比
  6) 延迟迁移正确率   = 同知识点在首次作答 ≥7 天后的后续作答正确率
                       （兼容显式 delayed_retest 事件；默认由同 KP 时间差派生）

设计为「纯函数聚合 + DB 薄层」，便于单测（构造事件流直接喂纯函数）。
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentEvent


# ---------------------------------------------------------------------------
# 取值助手：同时兼容 ORM 对象（属性）与原始 dict 载荷
# ---------------------------------------------------------------------------
def _g(ev: Any, key: str, default: Any = None) -> Any:
    if isinstance(ev, dict):
        v = ev.get(key, None)
    else:
        v = getattr(ev, key, None)
    return v if v is not None else default


def _as_dt(ev: Any) -> Optional[datetime]:
    return _g(ev, "occurred_at") or _g(ev, "created_at")


def _session_key(ev: Any) -> tuple:
    """会话 = 学生 × 题。无 content_hash 时退化为 题干文本。"""
    sid = _g(ev, "student_id") or "anon"
    ch = _g(ev, "content_hash")
    q = _g(ev, "question")
    return (sid, ch or q or "unknown")


# M2 体验红线（超线即在报表中标红）
RED_LINES = {
    "first_round_direct_rate": ("<=", 0.05),
    "hint_effective_progress_rate": (">=", 0.60),
    "avoidance_abandonment_rate": ("<=", 0.25),
    "frustration_rate": ("<=", 0.15),
}


def compute_p2_metrics(events: List[Any]) -> Dict[str, Any]:
    """纯函数：输入事件流，输出六项指标 + 原始计数。

    事件可为 ORM 对象或 dict；至少需含 student_id/content_hash/question/
    event_type/occurred_at 及各项埋点字段（缺省按 None/0 处理）。
    """
    events = list(events)

    # 1) 按 (学生×题) 分会话，并按时间升序排列
    sessions: Dict[tuple, List[Any]] = {}
    for ev in events:
        sessions.setdefault(_session_key(ev), []).append(ev)
    for k in sessions:
        sessions[k].sort(
            key=lambda e: _as_dt(e) or datetime.min.replace(tzinfo=timezone.utc)
        )

    # ---- M1 首轮直给率 ----
    system_sessions: List[tuple] = []
    first_give = 0
    for k, evs in sessions.items():
        if any(_g(e, "event_type") == "direct_reveal" for e in evs):
            continue  # 学生主动看解析 → 不计入系统直给的分母/分子（R6）
        system_sessions.append(k)
        first = evs[0]
        if _g(first, "event_type") == "solve" and _g(first, "intent") == "direct":
            first_give += 1
    m1 = (first_give / len(system_sessions)) if system_sessions else None

    # ---- M2 提示有效推进率 / M5 挫败信号率（遍历 Hint 轮）----
    hint_rounds = 0
    effective = 0
    frustration = 0
    for k, evs in sessions.items():
        for i, e in enumerate(evs):
            if _g(e, "event_type") != "hint_shown":
                continue
            hint_rounds += 1
            if (_g(e, "hint_frustration_streak") or 0) >= 2:
                frustration += 1
            nxt = evs[i + 1] if i + 1 < len(evs) else None
            if nxt is None:
                continue
            nxt_type = _g(nxt, "event_type")
            if nxt_type == "hint_progress":
                effective += 1
            elif nxt_type == "hint_shown" and (_g(nxt, "hint_level") or 0) > (
                _g(e, "hint_level") or 0
            ):
                effective += 1
            elif nxt_type == "solve" and _g(nxt, "correct") is True:
                effective += 1
    m2 = (effective / hint_rounds) if hint_rounds else None
    m5 = (frustration / hint_rounds) if hint_rounds else None

    # ---- M3 单题平均交互轮次 ----
    rounds_list: List[int] = []
    for k, evs in sessions.items():
        turns = sum(
            1
            for e in evs
            if _g(e, "event_type") in ("hint_shown", "direct_reveal", "solve")
        )
        if turns:
            rounds_list.append(turns)
    m3 = (sum(rounds_list) / len(rounds_list)) if rounds_list else None

    # ---- M4 规避/放弃率 ----
    avoidance = 0
    abandonment = 0
    for k, evs in sessions.items():
        types = [_g(e, "event_type") for e in evs]
        has_reveal = "direct_reveal" in types
        has_hint = "hint_shown" in types
        has_solve_ans = any(
            _g(e, "event_type") == "solve" and _g(e, "correct") is not None for e in evs
        )
        if has_reveal and has_solve_ans:
            avoidance += 1  # 看过答案后又提交作答 → 绕过引导
        elif has_hint and not has_solve_ans and not has_reveal:
            abandonment += 1  # 起了引导但既没作答也没看解析就离开
    total_sess = len(sessions)
    m4_avoid = (avoidance / total_sess) if total_sess else None
    m4_aband = (abandonment / total_sess) if total_sess else None
    m4 = ((avoidance + abandonment) / total_sess) if total_sess else None

    # ---- M6 延迟迁移正确率 ----
    # 每 (学生, 知识点) 记录首次作答时间；≥7 天后的后续作答计入延迟迁移样本
    kp_first: Dict[tuple, Optional[datetime]] = {}
    delayed: List[bool] = []
    for ev in events:
        if _g(ev, "event_type") != "solve":
            continue
        correct = _g(ev, "correct")
        if correct is None:
            continue
        kps = _g(ev, "knowledge_points") or []
        if isinstance(kps, str):
            kps = [kps]
        dt = _as_dt(ev)
        sid = _g(ev, "student_id") or "anon"
        for kp in kps:
            key = (sid, kp)
            if key not in kp_first:
                kp_first[key] = dt
            else:
                first = kp_first[key]
                if first and dt and (dt - first) >= timedelta(days=7):
                    delayed.append(bool(correct))
    m6 = (sum(delayed) / len(delayed)) if delayed else None

    return {
        "first_round_direct_rate": m1,
        "hint_effective_progress_rate": m2,
        "avg_rounds_per_question": m3,
        "avoidance_rate": m4_avoid,
        "abandonment_rate": m4_aband,
        "avoidance_abandonment_rate": m4,
        "frustration_rate": m5,
        "delayed_retention_rate": m6,
        "_counts": {
            "sessions": total_sess,
            "system_sessions": len(system_sessions),
            "first_give_sessions": first_give,
            "hint_rounds": hint_rounds,
            "effective_hints": effective,
            "frustration_signals": frustration,
            "avoidance_sessions": avoidance,
            "abandonment_sessions": abandonment,
            "delayed_solves": len(delayed),
        },
    }


def evaluate_red_lines(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """对照 M2 体验红线，给出每项是否超线（None 视为未触发、不判红）。"""
    flags = {}
    for name, (op, thr) in RED_LINES.items():
        val = metrics.get(name)
        if val is None:
            flags[name] = {"threshold": f"{op} {thr:.0%}", "value": None, "breached": False}
            continue
        breached = (val > thr) if op == "<=" else (val < thr)
        flags[name] = {
            "threshold": f"{op} {thr:.0%}",
            "value": round(val, 4),
            "breached": bool(breached),
        }
    return flags


# ---------------------------------------------------------------------------
# DB 薄层：按筛选条件取事件，交给纯函数聚合
# ---------------------------------------------------------------------------
async def compute_p2_metrics_for(
    db: AsyncSession,
    student_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    subject: Optional[str] = None,
) -> Dict[str, Any]:
    """查询 student_events 并聚合六项 P2 指标。

    不传 student_id 时按租户（tenant_id）/ 全量聚合（老师/管理员视角）。
    """
    stmt = select(StudentEvent)
    if student_id:
        stmt = stmt.where(StudentEvent.student_id == student_id)
    if tenant_id:
        stmt = stmt.where(StudentEvent.tenant_id == tenant_id)
    if from_dt:
        stmt = stmt.where(StudentEvent.occurred_at >= from_dt)
    if to_dt:
        stmt = stmt.where(StudentEvent.occurred_at <= to_dt)
    if subject:
        stmt = stmt.where(StudentEvent.subject == subject)

    res = await db.execute(stmt)
    events = list(res.scalars().all())
    return compute_p2_metrics(events)
