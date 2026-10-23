"""真题题库自召回评测：验证真实高考题灌库后的向量检索质量。

与 eval_retrieval.py 的区别：
  eval_retrieval.py 的 8 个 CASES 是手写的 demo 用例（subject='math'，知识点为中文名），
  只能验证链路通不通，无法反映真实题库的检索质量。
  本脚本用库里已有的真题（subject 可指定，默认 '数学'）做「自召回」评测：
  拿每题的题干去检索，看能否把该题自己找回来。

为什么用自召回而不是知识点比对：
  真题的 knowledge_point 存的是 DAG id（如 MA-B1-C1-S3-K02），不是中文名，
  无法直接做字符串比对；自召回率直接反映向量空间的质量，是更硬的指标。

用法（137 的 agent-backend 容器内）：
    docker exec -w /app edu-agent-backend python /app/scripts/eval_retrieval_realbank.py
    docker exec -w /app edu-agent-backend python /app/scripts/eval_retrieval_realbank.py --subject 数学

判据：TOP1 自命中率 ≥ 95% 视为向量质量可用（123 题真题实测 100%）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config, rag  # noqa: E402
import asyncpg  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", default="数学", help="course_kb 里的学科值，真题为 '数学'")
    ap.add_argument("--limit", type=int, default=0, help="只评测前 N 题，0=全部")
    args = ap.parse_args()

    await rag.warm()
    conn = await asyncpg.connect(os.environ["DB_DSN"])
    rows = await conn.fetch(
        "select stem from course_kb where subject=$1 order by id", args.subject
    )
    if args.limit:
        rows = rows[: args.limit]
    await conn.close()

    n = len(rows)
    if not n:
        raise SystemExit(f"course_kb 中 subject={args.subject!r} 无数据")

    print(f"embed_ready={config.embed_ready()} db_ready={config.db_ready()} TOP_K={config.TOP_K}")
    top1 = topk = 0
    miss: list[tuple[str, str, float]] = []
    for r in rows:
        out = await rag.retrieve(r["stem"], subject=args.subject)
        chunks = out["chunks"]
        key = r["stem"][:40]
        hit1 = bool(chunks) and (chunks[0]["text"] or "").startswith(key)
        if hit1:
            top1 += 1
        elif len(miss) < 8:
            miss.append((
                r["stem"][:34],
                chunks[0]["knowledge_point"] if chunks else "-",
                round(float(chunks[0]["score"]), 3) if chunks else 0.0,
            ))
        if any((ch["text"] or "").startswith(key) for ch in chunks):
            topk += 1

    print(f"\n=== 真题自召回评测（subject={args.subject}，{n} 题）===")
    print(f"  TOP1 自命中率 : {top1}/{n} = {top1 / n * 100:.1f}%")
    print(f"  TOP{config.TOP_K} 包含率  : {topk}/{n} = {topk / n * 100:.1f}%")
    if miss:
        print("  未自命中样例（题干 -> 召回的知识点 id [相似度]）:")
        for m in miss:
            print(f"    {m[0]} -> {m[1]} [{m[2]}]")


if __name__ == "__main__":
    asyncio.run(main())
