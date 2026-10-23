"""回填 course_kb.type，把之前混在一起的"选择"拆成 单选 / 多选。

背景：
  旧版 _norm_type 把 单选/多选/选择 一律归成"选择"，导致组卷无法控制题型配比、
  自适应无法区分难度模型。归一化逻辑已修，但**库里已灌的题仍是旧值**，需要回填。

匹配依据：import_bank 的 content_hash = sha256(f"{subject}|{stem}|{answer}")

用法（137 的 agent-backend 容器内，灌库 JSON 需已传入）：
    docker exec -w /app edu-agent-backend python /app/scripts/backfill_type.py \
        /tmp/bank_math.json /tmp/bank_phys.json

脚本读每个 JSON 的 学科/题干/答案/题型，逐条 UPDATE，幂等可重跑。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Iterable

import asyncpg

# 与 import_bank._norm_type 保持一致（实验题也必须单独区分，否则会被归到"解答"）
TYPE_CN = {
    "单选": "单选", "single": "单选",
    "多选": "多选", "multi": "多选",
    "选择": "选择",
    "实验": "实验", "experiment": "实验",
    "填空": "填空", "fill": "填空",
    "解答": "解答", "solve": "解答", "essay": "解答",
}


def content_hash(subject: str, stem: str, answer: str) -> str:
    return hashlib.sha256(f"{subject}|{stem}|{answer}".encode("utf-8")).hexdigest()


def load(path: str) -> list[tuple[str, str]]:
    recs = json.load(open(path, encoding="utf-8"))
    out = []
    for r in recs:
        t = r.get("题型") or r.get("type")
        if not t:
            continue
        t = TYPE_CN.get(str(t).strip(), str(t).strip())
        h = content_hash(
            str(r.get("学科") or r.get("subject") or ""),
            str(r.get("题干") or r.get("stem") or "").strip(),
            str(r.get("答案") or r.get("answer") or "").strip(),
        )
        out.append((h, t))
    return out


async def main(paths: Iterable[str]) -> None:
    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        pairs: list[tuple[str, str]] = []
        for p in paths:
            if not os.path.exists(p):
                print(f"  [warn] 跳过不存在的文件：{p}")
                continue
            got = load(p)
            print(f"  {os.path.basename(p)}: {len(got)} 条")
            pairs.extend(got)

        done = miss = 0
        for h, t in pairs:
            res = await conn.execute(
                "UPDATE course_kb SET type=$1 WHERE content_hash=$2", t, h
            )
            affected = int(res.split()[-1]) if res and res.startswith("UPDATE") else 0
            if affected:
                done += 1
            else:
                miss += 1

        print(f"\n回填 type：成功 {done}，未匹配 {miss}")
        print("course_kb 题型分布：")
        for r in await conn.fetch(
            "select subject, type, count(*) n from course_kb group by 1,2 order by 1,2"
        ):
            print(f"   {r['subject']:6} {r['type']:6} {r['n']} 题")
        legacy = await conn.fetchval("select count(*) from course_kb where type='选择'")
        print("仍为兜底值'选择'的题数:", legacy)
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: backfill_type.py <bank.json> [更多 bank.json ...]")
    asyncio.run(main(sys.argv[1:]))
