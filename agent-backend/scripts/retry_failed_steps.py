"""对跑批中 LLM 超时跳过的题目做顺序补跑（在 edu-agent-backend 容器内运行）。

背景：gen_pedagogy.py 全量跑批时，个别复杂解答题单条 LLM 调用超过 180s 超时，
重试 3 次仍失败后按设计跳过（打 [warn] 继续），导致这些题的 explanation 留空。
本脚本只针对指定 content_hash 列表，逐条顺序补跑（无并发争用），超时放宽到 300s，
写库逻辑与 gen_pedagogy.process_question 保持一致（仅在解析出非空内容时才 UPDATE）。

用法：
  python scripts/retry_failed_steps.py <content_hash> [<content_hash> ...]
  python scripts/retry_failed_steps.py --all-missing   # 自动查全库缺讲解的题
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

import gen_pedagogy as gp  # noqa: E402

TIMEOUT = 300  # 比全量跑批的 180s 更宽松，专治慢题


async def call_question(q: dict, steps: list[dict]) -> list[dict]:
    """与 gp._call_json 同链路，但超时 300s、重试 2 次（共 3 次尝试）。"""
    last = None
    for attempt in range(3):
        try:
            resp = await gp.client.chat.completions.create(
                model=gp.config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": gp.STEP_SYSTEM},
                    {"role": "user", "content": gp._step_user(q, steps)},
                ],
                temperature=0.3,
                timeout=TIMEOUT,
            )
            data = gp._parse_json(resp.choices[0].message.content or "")
            if data:
                return data.get("steps") or []
            last = RuntimeError("empty response")
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < 2:
            await asyncio.sleep(5 * (attempt + 1))
    print(f"  [warn] 补跑仍失败: {last!r}")
    return []


async def run(hashes: list[str]):
    dsn = os.environ.get("DB_DSN")
    if not dsn:
        raise SystemExit("未配置 DB_DSN。")
    if not gp.config.llm_ready():
        raise SystemExit("LLM 未就绪。")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    ok, fail = 0, 0
    for h in hashes:
        async with pool.acquire() as conn:
            q = await conn.fetchrow(
                "SELECT content_hash, subject, type, stem, options, answer, analysis, knowledge_points "
                "FROM course_kb WHERE content_hash=$1", h)
            steps = [dict(r) for r in await conn.fetch(
                "SELECT step_no, step_kind, label, text, is_correct, kp_name "
                "FROM course_kb_steps WHERE content_hash=$1 ORDER BY step_no", h)]
        if not q or not steps:
            print(f"[skip] {h[:8]} 无题目或无步骤")
            fail += 1
            continue
        out = await call_question(dict(q), steps)
        written = 0
        for i, s in enumerate(steps):
            o = gp._match_step(out, s["label"], i)
            exp = (o.get("explanation") or "").strip()
            err = (o.get("error_reason") or "").strip()
            if exp or err:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE course_kb_steps SET explanation=$1, error_reason=$2 "
                        "WHERE content_hash=$3 AND step_no=$4",
                        exp, err, h, s["step_no"])
                written += 1
        if written:
            ok += 1
            print(f"  [q✓] {h[:8]} {q['type']} 步数={len(steps)} 实写={written}", flush=True)
        else:
            fail += 1
            print(f"  [q✗] {h[:8]} {q['type']} 步数={len(steps)} 未写出任何内容", flush=True)
    await pool.close()
    print(f"[done] 成功 {ok} 题，失败 {fail} 题")
    return 0 if fail == 0 else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("hashes", nargs="*")
    p.add_argument("--all-missing", action="store_true",
                   help="自动查询全库仍缺 explanation 的题目并补跑")
    args = p.parse_args()

    async def collect():
        if args.all_missing:
            conn = await asyncpg.connect(os.environ["DB_DSN"])
            rows = await conn.fetch(
                "SELECT DISTINCT content_hash FROM course_kb_steps "
                "WHERE explanation IS NULL OR btrim(explanation)='' ORDER BY content_hash")
            await conn.close()
            return [r["content_hash"] for r in rows]
        return args.hashes

    hashes = asyncio.run(collect())
    if not hashes:
        print("没有需要补跑的题目。")
        return
    print(f"[plan] 待补跑 {len(hashes)} 题")
    raise SystemExit(asyncio.run(run(hashes)))


if __name__ == "__main__":
    main()
