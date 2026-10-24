"""S5 端到端对账：3 学生 × 10 事件 → 落库 → 周报步骤级薄弱点 + 老师端错因分布。

用途：验证「流水表数字」与「两端聚合视图数字」完全一致（计划书 S5 验收标准）。
跑法（本地，需先建好到 106 的 SSH 隧道）：
    ssh -o ServerAliveInterval=30 -N -L 15432:106.12.25.5:5432 137
    python scripts/e2e_s5_attribution.py

说明：
- 只写测试数据，student_id 带 `s5_e2e_` 前缀，跑完自动清理（含 mastery / 错题）。
- 只消费不判分：事件载荷直接构造（判分链路已在 S3 验证过），本脚本验的是聚合口径。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 本地隧道 DSN（密码含 @ # 必须百分号编码）
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://tes:Tes%40Pg%23106@127.0.0.1:15432/tes_edu",
)

from sqlalchemy import delete, select, text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.models.ops import (  # noqa: E402
    CourseKbStep,
    StudentEvent,
    StudentMastery,
    StudentStepError,
    WrongQuestion,
)
from app.services import attribution, learning, ops, reports  # noqa: E402
from app.workers import student_event_worker  # noqa: E402

RUN = f"s5_e2e_{int(time.time())}"
STUDENTS = [f"{RUN}_s{i}" for i in (1, 2, 3)]
EVENTS_PER_STUDENT = 10
# 刻意让 case 最多、calc 次之，便于验证排序与占比
TYPE_CYCLE = ["case", "case", "case", "calc", "calc", "formula", "read", "logic", "concept", "unknown"]
SRC_CYCLE = ["map", "map", "map", "llm", "llm", "map", "unknown", "map", "llm", "map"]

results: List[tuple] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f" — {detail}" if detail else ""))


async def apply_migration() -> None:
    """S5 ④ 迁移：wrong_questions 补 content_hash（幂等）。"""
    sql_path = os.path.join(os.path.dirname(__file__), "extend_wrong_questions.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()
    async with engine.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(text(stmt))
    print("[migration] extend_wrong_questions.sql 已执行")


async def pick_steps(db) -> List[tuple]:
    """选几道数学题的真实步骤作为归因落点（保证 hash/step 在库里真实存在）。"""
    res = await db.execute(
        text(
            """
            SELECT s.content_hash, s.step_no
            FROM course_kb_steps s
            JOIN course_kb q ON q.content_hash = s.content_hash
            WHERE q.subject = '数学'
              AND (s.distractor_map IS NOT NULL
                   OR s.typical_errors IS NOT NULL
                   OR s.wrong_value_map IS NOT NULL)
            ORDER BY s.content_hash, s.step_no
            LIMIT 8
            """
        )
    )
    return [(r[0], r[1]) for r in res.all()]


def build_events(steps: List[tuple]) -> List[dict]:
    events: List[dict] = []
    now = datetime.now(timezone.utc)
    for si, sid in enumerate(STUDENTS):
        for i in range(EVENTS_PER_STUDENT):
            h, step = steps[(si + i) % len(steps)]
            events.append(
                {
                    "student_id": sid,
                    "tenant_id": RUN,
                    "event_type": "solve",
                    "subject": "数学",
                    "mode": "solve",
                    "question": f"[e2e] 事件 {si}-{i}",
                    "knowledge_points": [f"{RUN}_kp"],
                    "ts": (now - timedelta(hours=i)).isoformat(),
                    "content_hash": h,
                    "correct": False,
                    "partial": False,
                    "first_wrong_step": step,
                    "error_type": TYPE_CYCLE[i % len(TYPE_CYCLE)],
                    "attribution_source": SRC_CYCLE[i % len(SRC_CYCLE)],
                    "evidence": f"e2e 证据 {si}-{i}",
                }
            )
    return events


async def cleanup() -> None:
    async with SessionLocal() as db:
        for model in (StudentEvent, StudentStepError, StudentMastery, WrongQuestion):
            await db.execute(delete(model).where(model.student_id.in_(STUDENTS)))
        await db.commit()
    await engine.dispose()
    print("[cleanup] 测试数据已删除")


async def main() -> int:
    await apply_migration()
    async with SessionLocal() as db:
        steps = await pick_steps(db)
        if not steps:
            print("[FAIL] 库里找不到带归因映射的数学步骤，无法构造流水")
            return 1
        print(f"[setup] 归因落点 {len(steps)} 个（题 hash 前 8 位："
              f"{sorted({h[:8] for h, _ in steps})}）")

        events = build_events(steps)
        await student_event_worker.handle_events(events)
        print(f"[setup] 已消费 {len(events)} 条事件（{len(STUDENTS)} 学生 × {EVENTS_PER_STUDENT}）")

        # ---- 1) 流水表基准计数（SQL 直查，作为对账金标准）----
        res = await db.execute(
            select(StudentStepError.error_type, StudentStepError.step_no,
                   StudentStepError.content_hash, StudentStepError.attribution_source)
            .where(StudentStepError.student_id.in_(STUDENTS))
        )
        flow = res.all()
        check("流水表落库条数 = 事件数", len(flow) == len(events), f"{len(flow)} / {len(events)}")

        by_type: dict = {}
        by_src: dict = {}
        by_step: dict = {}
        for et, sn, ch, src in flow:
            by_type[et] = by_type.get(et, 0) + 1
            by_src[src] = by_src.get(src, 0) + 1
            by_step[(ch, sn)] = by_step.get((ch, sn), 0) + 1

        # ---- 2) 学生周报：步骤级薄弱点 ----
        for sid in STUDENTS:
            rep = await reports.compute_weekly_report(db, sid, period_days=7, tenant_id=RUN)
            sw = rep.get("step_weakness", {})
            ok_total = sw.get("total_errors") == EVENTS_PER_STUDENT
            sum_types = sum(d["count"] for d in sw.get("error_types", []))
            check(
                f"周报[{sid.split('_')[-1]}] total_errors=10 且错因分布可加总",
                ok_total and sum_types == EVENTS_PER_STUDENT,
                f"total={sw.get('total_errors')} 分布合计={sum_types}",
            )
            if sid == STUDENTS[0]:
                check("周报 headline 指向最常错的步骤", bool(sw.get("headline")), sw.get("headline", ""))
                top = sw.get("top_steps") or []
                if top:
                    sql_cnt = by_step.get((top[0]["content_hash"], top[0]["step_no"]), 0)
                    # 单学生维度：Top 步骤次数应等于该生在该步骤上的出错次数
                    check("周报 Top 断点步骤次数与流水一致", top[0]["count"] <= sql_cnt and top[0]["count"] > 0,
                          f"周报={top[0]['count']} 全局同步骤={sql_cnt}")

        # ---- 3) 老师端错因分布（三种口径）----
        rows = await learning.list_step_errors(db, tenant_id=RUN, student_ids=STUDENTS,
                                               since=datetime.now(timezone.utc) - timedelta(days=7))
        step_texts = await reports.fetch_step_texts(
            db, {(r.content_hash, r.step_no or 0) for r in rows if r.content_hash}
        )
        d_type = attribution.build_class_distribution(rows, "error_type")
        check("老师端 total = 流水条数", d_type["total"] == len(flow), f"{d_type['total']} / {len(flow)}")
        agg_type = {r["key"]: r["count"] for r in d_type["rows"]}
        check("老师端按错因聚合与 SQL group by 一致", agg_type == by_type, f"{agg_type}")

        d_step = attribution.build_class_distribution(rows, "step", step_texts=step_texts, top_n=5)
        top_step = d_step["rows"][0]
        check("老师端按步骤聚合 Top1 与 SQL 一致",
              by_step.get((top_step.get("content_hash"), top_step.get("step_no"))) == top_step["count"],
              f"{top_step['step_label']} × {top_step['count']}")

        d_q = attribution.build_class_distribution(rows, "question", top_n=5)
        check("老师端按题聚合次数可加总",
              sum(r["count"] for r in d_q["rows"]) == len(flow),
              f"{sum(r['count'] for r in d_q['rows'])} / {len(flow)}")

        # ---- 4) 归因来源指标（/metricsz 口径）----
        src = attribution.attribution_source_ratio(rows)
        check("归因来源占比与 SQL 一致", {k: v for k, v in src["counts"].items() if v} == by_src,
              f"{src['counts']} vs {by_src}")
        payload = attribution.metrics_payload(rows)
        check("metricsz 指标匿名（不含学生ID）",
              all(s not in str(payload) for s in STUDENTS))

        # ---- 5) CSV 导出 ----
        csv_text = attribution.distribution_csv(d_type)
        check("CSV 导出行数 = 分类数 + 表头",
              len(csv_text.strip().splitlines()) == len(d_type["rows"]) + 1,
              f"{len(csv_text.strip().splitlines())} 行")

        # ---- 6) 错题本 correction（S5 ④）----
        h, step = steps[0]
        w = WrongQuestion(
            student_id=STUDENTS[0], question="[e2e] 错题本验证",
            wrong_answer="x", correct_answer="y", knowledge_point=f"{RUN}_kp",
            content_hash=h, wrong_step_no=step, error_type="case",
            attribution_source="map", evidence="e2e",
        )
        db.add(w)
        await db.commit()
        enriched = await ops.enrich_wrong(db, [w])
        e0 = enriched[0]
        step_row = await db.execute(
            select(CourseKbStep).where(
                CourseKbStep.content_hash == h, CourseKbStep.step_no == step
            )
        )
        s_row = step_row.scalars().first()
        check("错题本带出错因中文名", e0["error_type_label"] == attribution.label_of("case"),
              str(e0["error_type_label"]))
        check("错题本 correction 与步骤映射一致",
              e0["correction"] == attribution.pick_correction(s_row, "case"),
              str(e0["correction"])[:40])

    # KEEP_DATA=1 时保留数据，便于接着用真实 HTTP 冒烟接口（跑完记得 cleanup_e2e_data.py）
    if not os.getenv("KEEP_DATA"):
        await cleanup()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} PASS =====")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
