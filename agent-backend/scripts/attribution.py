"""步骤级归因·运行时装配（在 edu-agent-backend 容器内运行）。

给定题目 content_hash 与学生答错的步骤 step_no，返回可直接下发给前端/教师的
教学归因包：
  - failed_step: 该步的 详解(explanation) + 常见错误原因(error_reason)
  - step_kp:     该步关联知识点的 理解/记忆/触类旁通（来自 kb_knowledge_points）
  - question_kps: 整题涉及的全部知识点及其 理解/记忆/触类旁通（满足"整个题目要用到哪些知识点"）

CLI 示例：
  python scripts/attribution.py --hash <content_hash> --step <step_no>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402


async def assemble(content_hash: str, failed_step_no: int | None = None) -> dict:
    dsn = os.environ.get("DB_DSN")
    if not dsn:
        raise SystemExit("未配置 DB_DSN。")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)

    out: dict = {"content_hash": content_hash}
    try:
        async with pool.acquire() as conn:
            q = await conn.fetchrow(
                "SELECT subject, type, stem, answer, analysis, knowledge_points, source_tag "
                "FROM course_kb WHERE content_hash=$1", content_hash)
            if not q:
                return {"error": "题目不存在"}
            q = dict(q)
            out["question"] = {
                "subject": q["subject"], "type": q["type"], "stem": q["stem"],
                "answer": q["answer"], "analysis": q["analysis"],
            }

            # 整题知识点（course_kb.knowledge_points 是 json 数组）
            kp_codes = []
            raw_kps = q.get("knowledge_points")
            if isinstance(raw_kps, str):
                try:
                    raw_kps = json.loads(raw_kps)
                except Exception:
                    raw_kps = []
            for k in (raw_kps or []):
                c = (k.get("code") or k.get("id")) if isinstance(k, dict) else k
                if c:
                    kp_codes.append(str(c))

            # 关联知识点 pedagogy
            if kp_codes:
                recs = await conn.fetch(
                    "SELECT code, title, understanding, memory, analogy "
                    "FROM kb_knowledge_points WHERE code = ANY($1)", kp_codes)
                out["question_kps"] = [dict(r) for r in recs]
            else:
                out["question_kps"] = []

            # 步骤 + 失败步
            steps = await conn.fetch(
                "SELECT step_no, step_kind, label, text, is_correct, kp_code, kp_name, "
                "explanation, error_reason FROM course_kb_steps WHERE content_hash=$1 ORDER BY step_no",
                content_hash)
            out["steps"] = [dict(s) for s in steps]

            target = None
            if failed_step_no is not None:
                target = next((s for s in steps if s["step_no"] == failed_step_no), None)
            elif steps:
                # 默认取第一个有 error_reason 的步骤演示
                target = next((s for s in steps if s.get("error_reason")), steps[0])
            out["failed_step"] = dict(target) if target else None

            if target and target.get("kp_code"):
                kp = await conn.fetchrow(
                    "SELECT code, title, understanding, memory, analogy "
                    "FROM kb_knowledge_points WHERE code=$1", target["kp_code"])
                out["step_kp"] = dict(kp) if kp else None
            else:
                out["step_kp"] = None
    finally:
        await pool.close()
    return out


def _pretty(d: dict) -> str:
    if "error" in d:
        return f"[错误] {d['error']}"
    lines = []
    q = d.get("question", {})
    lines.append(f"【题目】({q.get('subject')}/{q.get('type')})")
    lines.append(f"题干: {str(q.get('stem',''))[:120]}")
    lines.append(f"参考答案: {q.get('answer')}")
    lines.append("")
    fs = d.get("failed_step")
    if fs:
        lines.append(f"▶ 定位到失败步骤 #{fs['step_no']} ({fs.get('label')})")
        lines.append(f"  步骤内容: {fs.get('text')}")
        if fs.get("explanation"):
            lines.append(f"  详解(推导+知识点串联): {fs['explanation']}")
        if fs.get("error_reason"):
            lines.append(f"  常见错误原因: {fs['error_reason']}")
        kp = d.get("step_kp")
        if kp:
            lines.append(f"  ── 该步知识点【{kp.get('title')}】")
            if kp.get("understanding"):
                lines.append(f"     如何理解: {kp['understanding']}")
            if kp.get("memory"):
                lines.append(f"     记忆方法: {kp['memory']}")
            if kp.get("analogy"):
                lines.append(f"     触类旁通: {kp['analogy']}")
        lines.append("")
    qk = d.get("question_kps") or []
    if qk:
        lines.append(f"▶ 整题知识点（共 {len(qk)} 个）：")
        for kp in qk:
            lines.append(f"  · {kp.get('title')}")
            if kp.get("understanding"):
                lines.append(f"      理解: {kp['understanding'][:160]}")
            if kp.get("memory"):
                lines.append(f"      记忆: {kp['memory'][:120]}")
            if kp.get("analogy"):
                lines.append(f"      迁移: {kp['analogy'][:120]}")
    return "\n".join(lines)


async def _cli():
    p = argparse.ArgumentParser()
    p.add_argument("--hash", required=True)
    p.add_argument("--step", type=int, default=None)
    a = p.parse_args()
    d = await assemble(a.hash, a.step)
    print(_pretty(d))


if __name__ == "__main__":
    asyncio.run(_cli())
