"""T2 端到端对账：错题本自动写入链路（判分出错 → wrong_questions，幂等）。

用途：验证 T2 计划书验收标准——
  1) 3 学生 × 10 事件消费后，wrong_questions 新增数 = 错题事件数 − need_process 条数；
  2) 重复消费同一批事件，错题本条数不增长（幂等）；
  3) 错题本列表（管理端口径）含 correction / 步骤文本 / 知识点卡。
跑法（本地，需先建好到 106 的 SSH 隧道，建+用须同一条命令）：
    ssh -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -f -N -L 15432:106.12.25.5:5432 137
    python scripts/e2e_t2_wrong_questions.py

说明：
- 只写测试数据，student_id 带 `t2_e2e_` 前缀，跑完自动清理（含事件/流水/掌握度/错题）。
- 事件载荷直接构造（判分链路已在 S3 验证），本脚本验的是消费端落库口径；
  经 student_event_worker.handle_events 消费，顺带验证 worker 接线。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 本地隧道 DSN（密码含 @ # 必须百分号编码）
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://tes:Tes%40Pg%23106@127.0.0.1:15432/tes_edu",
)

from sqlalchemy import delete, func, select, text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.models.ops import (  # noqa: E402
    CourseKbStep,
    StudentEvent,
    StudentMastery,
    StudentStepError,
    WrongQuestion,
)
from app.services import attribution, learning, ops  # noqa: E402
from app.workers import student_event_worker  # noqa: E402

RUN = f"t2_e2e_{int(time.time())}"
STUDENTS = [f"{RUN}_s{i}" for i in (1, 2, 3)]
EVENTS_PER_STUDENT = 10
# 每生 10 条：6 条判错(完整归因) + 1 条 need_process(必须排除) + 1 条无 hash 判错(仍记)
#           + 2 条判对(排除)  => 期望错题本 7 条/生，共 21 条
EXPECTED_WRONG_PER_STUDENT = 7
TYPE_CYCLE = ["case", "calc", "formula", "read", "logic", "concept"]
NEED_PROCESS_Q = "[e2e] need_process 事件（不得入错题本）"

results: List[tuple] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f" — {detail}" if detail else ""))


async def apply_migration() -> None:
    """T2 依赖的列由 S5 迁移（extend_wrong_questions.sql）补齐，幂等重跑。"""
    sql_path = os.path.join(os.path.dirname(__file__), "extend_wrong_questions.sql")
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()
    async with engine.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(text(stmt))
    print("[migration] extend_wrong_questions.sql 已执行（幂等）")


async def pick_steps(db) -> List[Tuple[str, int]]:
    """选真实数学步骤作为归因落点（保证 hash/step 在库中存在，correction 可关联）。"""
    res = await db.execute(
        text(
            """
            SELECT s.content_hash, s.step_no
            FROM course_kb_steps s
            JOIN course_kb q ON q.content_hash = s.content_hash
            WHERE q.subject = '数学'
              AND (s.distractor_map IS NOT NULL OR s.typical_errors IS NOT NULL)
            ORDER BY s.content_hash, s.step_no
            LIMIT 8
            """
        )
    )
    return [(r[0], r[1]) for r in res.all()]


def build_events(steps: List[Tuple[str, int]]) -> List[dict]:
    events: List[dict] = []
    now = datetime.now(timezone.utc)
    for si, sid in enumerate(STUDENTS):
        for i in range(EVENTS_PER_STUDENT):
            h, step = steps[(si + i) % len(steps)]
            if i < 6:  # 判错 + 完整归因
                events.append(
                    {
                        "student_id": sid, "tenant_id": RUN, "event_type": "solve",
                        "subject": "数学", "mode": "solve",
                        "question": f"[e2e] 错题 {si}-{i}",
                        "knowledge_points": [f"{RUN}_kp"],
                        "ts": (now - timedelta(hours=i)).isoformat(),
                        "content_hash": h, "correct": False, "partial": False,
                        "first_wrong_step": step,
                        "error_type": TYPE_CYCLE[i % len(TYPE_CYCLE)],
                        "attribution_source": "map" if i % 2 else "llm",
                        "evidence": f"e2e 证据 {si}-{i}",
                        "student_answer": f"e2e 学生作答 {si}-{i}",
                    }
                )
            elif i == 6:  # 过程缺失：correct=False + need_process=True，必须被排除
                events.append(
                    {
                        "student_id": sid, "tenant_id": RUN, "event_type": "solve",
                        "subject": "数学", "mode": "solve",
                        "question": NEED_PROCESS_Q,
                        "knowledge_points": [f"{RUN}_kp"],
                        "ts": (now - timedelta(hours=i)).isoformat(),
                        "content_hash": h, "correct": False, "partial": False,
                        "first_wrong_step": step, "error_type": "case",
                        "attribution_source": "map", "evidence": "空过程",
                        "need_process": True,
                    }
                )
            elif i == 7:  # 判错但无 hash：仍记错题（correction 为 null，读取端容忍）
                events.append(
                    {
                        "student_id": sid, "tenant_id": RUN, "event_type": "solve",
                        "subject": "数学", "mode": "solve",
                        "question": f"[e2e] 无hash错题 {si}",
                        "knowledge_points": [f"{RUN}_kp"],
                        "ts": (now - timedelta(hours=i)).isoformat(),
                        "content_hash": "", "correct": False, "partial": False,
                        "first_wrong_step": None, "error_type": "case",
                        "attribution_source": "map", "evidence": "无 hash",
                        "student_answer": "B",
                    }
                )
            else:  # 判对：不进错题本
                events.append(
                    {
                        "student_id": sid, "tenant_id": RUN, "event_type": "solve",
                        "subject": "数学", "mode": "solve",
                        "question": f"[e2e] 答对 {si}-{i}",
                        "knowledge_points": [f"{RUN}_kp"],
                        "ts": (now - timedelta(hours=i)).isoformat(),
                        "content_hash": h, "correct": True, "partial": False,
                        "first_wrong_step": None, "error_type": None,
                        "attribution_source": None, "evidence": None,
                    }
                )
    return events


async def count_wrong(db) -> int:
    res = await db.execute(
        select(func.count()).select_from(WrongQuestion).where(
            WrongQuestion.student_id.in_(STUDENTS)
        )
    )
    return int(res.scalar_one())


async def cleanup() -> None:
    async with SessionLocal() as db:
        for model in (WrongQuestion, StudentStepError, StudentEvent, StudentMastery):
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
        print(f"[setup] 归因落点 {len(steps)} 个")

        events = build_events(steps)
        expected_total = EXPECTED_WRONG_PER_STUDENT * len(STUDENTS)

        # ---- 首轮消费（经 worker 全链路：事件 + 错因流水 + 掌握度 + 错题本）----
        await student_event_worker.handle_events(events)
        first = await count_wrong(db)
        check(
            "错题本条数 = 错题事件数 − need_process 条数",
            first == expected_total,
            f"{first} / {expected_total}（30 事件 = 18 判错 + 3 need_process + 3 无hash + 6 答对）",
        )

        # ---- 幂等：重复消费同一批事件 ----
        await student_event_worker.handle_events(events)
        second = await count_wrong(db)
        check("重复消费后错题本条数不增长（幂等）", second == first, f"{second} / {first}")

        # ---- 每生口径 + need_process 排除 + 无 hash 仍记 ----
        for sid in STUDENTS:
            res = await db.execute(
                select(WrongQuestion).where(WrongQuestion.student_id == sid)
            )
            rows = list(res.scalars().all())
            ok = len(rows) == EXPECTED_WRONG_PER_STUDENT
            no_hash = sum(1 for r in rows if not r.content_hash)
            check(
                f"学生[{sid.split('_')[-1]}] 错题 7 条（含 1 条无 hash）",
                ok and no_hash == 1,
                f"共 {len(rows)}，无 hash {no_hash}",
            )
        res = await db.execute(
            select(func.count()).select_from(WrongQuestion).where(
                WrongQuestion.student_id.in_(STUDENTS),
                WrongQuestion.question == NEED_PROCESS_Q,
            )
        )
        check("need_process 事件未入错题本", int(res.scalar_one()) == 0)

        # ---- 管理端口径：enrich 返回 correction / 错因中文名 / 知识点卡 ----
        res = await db.execute(
            select(WrongQuestion)
            .where(WrongQuestion.student_id == STUDENTS[0], WrongQuestion.content_hash.isnot(None))
            .limit(1)
        )
        w = res.scalars().first()
        enriched = await ops.enrich_wrong(db, [w])
        e0 = enriched[0]
        check("错题本含错因中文名", e0.get("error_type_label") == attribution.label_of(w.error_type),
              str(e0.get("error_type_label")))
        s_res = await db.execute(
            select(CourseKbStep).where(
                CourseKbStep.content_hash == w.content_hash,
                CourseKbStep.step_no == w.wrong_step_no,
            )
        )
        s_row = s_res.scalars().first()
        check(
            "correction 与步骤映射一致（管理端 GET /wrong-questions 同口径）",
            e0.get("correction") == attribution.pick_correction(s_row, w.error_type),
            str(e0.get("correction"))[:40],
        )
        check("wrong_answer 取学生原始作答", w.wrong_answer.startswith("e2e 学生作答"), str(w.wrong_answer))

    if not os.getenv("KEEP_DATA"):
        await cleanup()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} PASS =====")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
