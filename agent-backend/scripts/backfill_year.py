"""回填 course_kb.year。

为什么需要：
  course_kb 原本没有年份列（import_bank 也不写），真题灌进去后无法按年份筛选/
  统计，而"哪一年的题"对组卷、考情分析都是基本维度。
  本脚本先 ALTER TABLE 加 year 列（若不存在），再按 content_hash 回填。

匹配依据：
  import_bank 的 content_hash = sha256(f"{subject}|{stem}|{answer}")
  —— 与灌库时的归一化值一致，可精确反查。

用法（137 的 agent-backend 容器内，灌库 JSON 需已传入）：
    docker exec -w /app edu-agent-backend python /app/scripts/backfill_year.py \
        /tmp/bank_math.json /tmp/bank_phys.json

脚本会读每个 JSON 的 学科/题干/答案/年份，逐条 UPDATE，幂等可重跑。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Iterable

import asyncpg


def content_hash(subject: str, stem: str, answer: str) -> str:
    raw = f"{subject}|{stem}|{answer}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load(path: str) -> list[tuple[str, int]]:
    """返回 [(content_hash, year)]"""
    recs = json.load(open(path, encoding="utf-8"))
    out = []
    for r in recs:
        y = r.get("年份") or r.get("year")
        if not y:
            continue
        h = content_hash(
            str(r.get("学科") or r.get("subject") or ""),
            str(r.get("题干") or r.get("stem") or "").strip(),
            str(r.get("答案") or r.get("answer") or "").strip(),
        )
        out.append((h, int(y)))
    return out


async def main(paths: Iterable[str]) -> None:
    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        await conn.execute("ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS year int")

        pairs: list[tuple[str, int]] = []
        for p in paths:
            if not os.path.exists(p):
                print(f"  [warn] 跳过不存在的文件：{p}")
                continue
            got = load(p)
            print(f"  {os.path.basename(p)}: {len(got)} 条")
            pairs.extend(got)

        done = miss = 0
        for h, y in pairs:
            res = await conn.execute(
                "UPDATE course_kb SET year=$1 WHERE content_hash=$2 AND year IS NULL", y, h
            )
            affected = int(res.split()[-1]) if res and res.startswith("UPDATE") else 0
            if affected:
                done += 1
            else:
                miss += 1

        print(f"\n回填 year：成功 {done}，未匹配 {miss}")
        rows = await conn.fetch(
            "select subject, year, count(*) n from course_kb group by 1,2 order by 1,2"
        )
        print("course_kb 按 学科/年份 分布：")
        for r in rows:
            print(f"   {r['subject']:6} {r['year']}  {r['n']} 题")
        print("仍缺年份的题数:", await conn.fetchval("select count(*) from course_kb where year is null"))
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: backfill_year.py <bank.json> [更多 bank.json ...]")
    asyncio.run(main(sys.argv[1:]))
