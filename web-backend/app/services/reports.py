"""家长端学情周报计算（真实数据，来源 student_mastery + student_events）。

设计：
- compute_weekly_report：异步、连库，负责查询后调用 _build_report。
- _build_report：纯函数（不连库），输入「列表 + 周期 + 当前时间」，输出报告 dict。
  抽成纯函数以便无 DB 依赖的单元测试覆盖（与 web-backend 现有测试哲学一致）。
- _render_html：把报告渲染成自包含 HTML 页（内联 CSS，无外部依赖），供家长在网关后直接打开。

说明：
- 学生维度用 student_id 检索；tenant_id 为可选查询参数（安全层当前不回传 tenant，
  多租户强制隔离留待「家长↔学生绑定表」落地后补，见 P4 安全项）。
- 事件暂无 duration 字段，学习时长以「活跃天数 + 练习次数 + 估算分钟」体现（EST_MIN_PER_ACTION）。
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentEvent, StudentMastery

# 掌握度阈值：低于该值视为薄弱
WEAK_THRESHOLD = 0.6
# 掌握度阈值：达到该值视为已掌握
MASTERED_THRESHOLD = 0.8
# 单次练习动作估算时长（分钟）；事件暂无 duration 字段时的近似
EST_MIN_PER_ACTION = 3


def _mastery_of(m) -> Optional[float]:
    """由对错计数计算掌握度；无判分记录（judged=0）返回 None。"""
    judged = (m.correct_count or 0) + (m.wrong_count or 0)
    if judged == 0:
        return None
    return round((m.correct_count or 0) / judged, 4)


def _build_report(
    student_id: str,
    masteries: list,
    events: list,
    period_days: int,
    now: Optional[datetime] = None,
) -> dict:
    now = now or datetime.now(timezone.utc)

    items: List[dict] = []
    with_value: List[tuple] = []
    for m in masteries:
        mastery = _mastery_of(m)
        items.append(
            {
                "knowledge_point": m.knowledge_point,
                "subject": m.subject,
                "practice_count": m.practice_count or 0,
                "correct_count": m.correct_count or 0,
                "wrong_count": m.wrong_count or 0,
                "mastery": mastery,
                "last_practiced_at": m.last_practiced_at,
            }
        )
        if mastery is not None:
            with_value.append((m.knowledge_point, mastery, m.practice_count or 0))

    avg_mastery = (
        round(sum(v[1] for v in with_value) / len(with_value), 4)
        if with_value
        else None
    )
    mastered_count = sum(1 for v in with_value if v[1] >= MASTERED_THRESHOLD)
    weak = [(kp, mv, pc) for kp, mv, pc in with_value if mv < WEAK_THRESHOLD]
    weak.sort(key=lambda x: x[1])  # 最弱在前
    weak_points = [
        {"knowledge_point": kp, "mastery": mv, "practice_count": pc} for kp, mv, pc in weak
    ]

    # 学习投入（本周事件）
    since = now - timedelta(days=period_days)
    period_events = [e for e in events if e.occurred_at and e.occurred_at >= since]
    active_days = len(
        {
            (e.occurred_at.year, e.occurred_at.month, e.occurred_at.day)
            for e in period_events
            if e.occurred_at
        }
    )
    action_count = len(period_events)
    est_minutes = action_count * EST_MIN_PER_ACTION
    total_practice = sum(m.practice_count or 0 for m in masteries)

    # 建议
    suggestions: List[str] = []
    if weak_points:
        top = "、".join(wp["knowledge_point"] for wp in weak_points[:3])
        suggestions.append(f"本周薄弱知识点：{top}，建议优先针对性练习。")
    if avg_mastery is not None:
        if avg_mastery >= MASTERED_THRESHOLD:
            suggestions.append("整体掌握良好，保持练习节奏，挑战更高难度题。")
        elif avg_mastery < WEAK_THRESHOLD:
            suggestions.append("整体掌握度偏低，建议放慢进度、回归基础概念。")
        else:
            suggestions.append("掌握度中等，巩固已会知识点并逐步拓展。")
    if active_days == 0:
        suggestions.append("本周暂无练习记录，鼓励孩子每天保持少量练习。")
    if not suggestions:
        suggestions.append("继续保持，定期复习薄弱点。")

    summary_parts = [f"共覆盖 {len(items)} 个知识点"]
    if avg_mastery is not None:
        summary_parts.append(f"平均掌握度 {avg_mastery:.0%}")
    summary_parts.append(f"已掌握 {mastered_count} 个、薄弱 {len(weak_points)} 个")
    summary_parts.append(f"本周活跃 {active_days} 天、练习 {action_count} 次（约 {est_minutes} 分钟）")
    summary = "；".join(summary_parts) + "。"

    recent_events = [
        {
            "event_type": e.event_type,
            "subject": e.subject,
            "question": (e.question or "")[:80],
            "knowledge_points": e.knowledge_points or [],
            "occurred_at": e.occurred_at,
        }
        for e in events[:10]
    ]

    return {
        "student_id": student_id,
        "period_days": period_days,
        "generated_at": now.isoformat(),
        "summary": summary,
        "total_knowledge_points": len(items),
        "avg_mastery": avg_mastery,
        "mastered_count": mastered_count,
        "weak_count": len(weak_points),
        "weak_points": weak_points,
        "items": items,
        "study": {
            "active_days": active_days,
            "action_count": action_count,
            "est_minutes": est_minutes,
            "total_practice_count": total_practice,
        },
        "recent_events": recent_events,
        "suggestions": suggestions,
    }


async def compute_weekly_report(
    db: AsyncSession,
    student_id: str,
    period_days: int = 7,
    tenant_id: Optional[str] = None,
) -> dict:
    """查询掌握度与（本周）学情事件，调用 _build_report 生成报告。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=period_days)

    m_stmt = select(StudentMastery).where(StudentMastery.student_id == student_id)
    if tenant_id:
        m_stmt = m_stmt.where(StudentMastery.tenant_id == tenant_id)
    m_res = await db.execute(m_stmt)
    masteries = list(m_res.scalars().all())

    e_stmt = (
        select(StudentEvent)
        .where(StudentEvent.student_id == student_id)
        .where(StudentEvent.occurred_at >= since)
    )
    if tenant_id:
        e_stmt = e_stmt.where(StudentEvent.tenant_id == tenant_id)
    e_res = await db.execute(e_stmt)
    events = list(e_res.scalars().all())

    return _build_report(student_id, masteries, events, period_days, now)


# ----------------------------------------------------------------------------
# HTML 渲染（自包含报告页，供家长在网关后直接打开）
# ----------------------------------------------------------------------------
import html  # noqa: E402  (放在文件末避免与上方 heavy import 抢位置)


def _pct(v: Optional[float]) -> str:
    return f"{v:.0%}" if isinstance(v, (int, float)) else "—"


def _bar_color(mastery: Optional[float]) -> str:
    if mastery is None:
        return "#9ca3af"
    if mastery >= MASTERED_THRESHOLD:
        return "#16a34a"
    if mastery >= WEAK_THRESHOLD:
        return "#d97706"
    return "#dc2626"


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _render_html(report: dict) -> str:
    student_id = _esc(report["student_id"])
    period = report["period_days"]
    avg = report["avg_mastery"]
    total = report["total_knowledge_points"]
    mastered = report["mastered_count"]
    weak_n = report["weak_count"]
    study = report.get("study", {})
    suggestions = "".join(f"<li>{_esc(s)}</li>" for s in report.get("suggestions", []))

    # 概览卡片
    cards = f"""
      <div class="card"><div class="num">{total}</div><div class="lbl">知识点</div></div>
      <div class="card"><div class="num">{_pct(avg)}</div><div class="lbl">平均掌握度</div></div>
      <div class="card"><div class="num" style="color:#16a34a">{mastered}</div><div class="lbl">已掌握</div></div>
      <div class="card"><div class="num" style="color:#dc2626">{weak_n}</div><div class="lbl">薄弱点</div></div>
    """

    # 学习投入
    study_html = f"""
      <div class="study">
        <span>活跃 <b>{study.get('active_days',0)}</b> 天</span>
        <span>练习 <b>{study.get('action_count',0)}</b> 次</span>
        <span>约 <b>{study.get('est_minutes',0)}</b> 分钟</span>
        <span>累计练习 <b>{study.get('total_practice_count',0)}</b> 次</span>
      </div>
      <p class="hint">注：事件暂无单次时长字段，时长为「练习次数 × {EST_MIN_PER_ACTION} 分钟」估算值。</p>
    """

    # 薄弱点（按掌握度升序，最弱在前）
    weak_rows = ""
    for w in report.get("weak_points", []):
        mv = w["mastery"]
        pct = int(round((mv or 0) * 100))
        color = _bar_color(mv)
        weak_rows += f"""
          <div class="row">
            <div class="name">{_esc(w['knowledge_point'])}</div>
            <div class="track"><div class="fill" style="width:{pct}%;background:{color}"></div></div>
            <div class="val" style="color:{color}">{_pct(mv)} · 练{w['practice_count']}次</div>
          </div>"""
    weak_block = (
        weak_rows
        if weak_rows
        else '<p class="hint">本周暂无薄弱点，继续保持 🎉</p>'.replace("🎉", "")
    )

    # 全部知识点（紧凑列表，最多 40 条）
    kp_rows = ""
    for it in report.get("items", [])[:40]:
        mv = it["mastery"]
        pct = int(round((mv or 0) * 100))
        color = _bar_color(mv)
        label = _pct(mv) if mv is not None else "练习中"
        kp_rows += f"""
          <div class="row">
            <div class="name">{_esc(it['knowledge_point'])}</div>
            <div class="track"><div class="fill" style="width:{pct}%;background:{color}"></div></div>
            <div class="val" style="color:{color}">{label} · 练{it['practice_count']}次</div>
          </div>"""
    kp_block = kp_rows or '<p class="hint">暂无知识点练习记录。</p>'

    # 近期练习
    ev_rows = ""
    for e in report.get("recent_events", []):
        kps = "、".join(e.get("knowledge_points") or [])
        ev_rows += f"""
          <div class="ev">
            <span class="tag">{_esc(e.get('event_type',''))}</span>
            <span class="q">{_esc(e.get('question',''))}</span>
            <span class="kp">{_esc(kps)}</span>
          </div>"""
    ev_block = ev_rows or '<p class="hint">暂无练习事件。</p>'

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>学情周报 · {student_id}</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:#f5f7fb;color:#1f2937;padding:16px}}
  .wrap{{max-width:720px;margin:0 auto}}
  h1{{font-size:20px;margin:8px 0 2px}}
  .sub{{color:#6b7280;font-size:13px;margin-bottom:14px}}
  .cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}}
  .card{{background:#fff;border-radius:12px;padding:14px 8px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
  .card .num{{font-size:24px;font-weight:700}}
  .card .lbl{{font-size:12px;color:#6b7280;margin-top:4px}}
  section{{background:#fff;border-radius:12px;padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
  section h2{{font-size:15px;margin:0 0 12px;border-left:4px solid #2563eb;padding-left:8px}}
  .study{{display:flex;flex-wrap:wrap;gap:14px;font-size:14px}}
  .study b{{font-size:16px;color:#111827}}
  .row{{display:grid;grid-template-columns:120px 1fr 110px;align-items:center;gap:8px;margin:8px 0}}
  .name{{font-size:13px;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .track{{background:#eef2f7;border-radius:6px;height:10px;overflow:hidden}}
  .fill{{height:100%;border-radius:6px}}
  .val{{font-size:12px;text-align:right;color:#6b7280}}
  .hint{{font-size:12px;color:#9ca3af;margin:6px 0 0}}
  ul{{margin:6px 0;padding-left:20px;font-size:14px;line-height:1.7}}
  .ev{{display:flex;gap:8px;align-items:baseline;font-size:13px;padding:6px 0;border-bottom:1px solid #f1f5f9}}
  .ev .tag{{background:#e0edff;color:#1d4ed8;border-radius:6px;padding:1px 6px;font-size:11px;white-space:nowrap}}
  .ev .q{{flex:1;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  .ev .kp{{color:#9ca3af;font-size:11px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
  footer{{font-size:11px;color:#9ca3af;text-align:center;margin-top:8px;line-height:1.6}}
</style></head>
<body><div class="wrap">
  <h1>学情周报</h1>
  <div class="sub">学生 {student_id} · 近 {period} 天 · 生成于 {_esc(report.get('generated_at',''))}</div>
  <div class="cards">{cards}</div>
  <section><h2>一句话总结</h2><p style="margin:0;font-size:14px;line-height:1.7">{_esc(report.get('summary',''))}</p></section>
  <section><h2>学习投入</h2>{study_html}</section>
  <section><h2>薄弱点（优先攻克）</h2>{weak_block}</section>
  <section><h2>知识点掌握度总览</h2>{kp_block}</section>
  <section><h2>给家长的建议</h2><ul>{suggestions}</ul></section>
  <section><h2>近期练习</h2>{ev_block}</section>
  <footer>数据来源：练习记录与学情事件（真实数据）。本页仅供家长查看，请勿外传。<br>
  安全说明：当前 report 按 student_id 检索，家长↔学生绑定与租户强制隔离待 P4 安全项补全。</footer>
</div></body></html>"""
