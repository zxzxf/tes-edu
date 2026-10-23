"""统一 course_kb_embeddings 的 subject 口径，并把知识点 id 回填成中文名。

解决两个分裂：
  1) subject：demo 种子数据用 'math'，真题用 '数学'/'物理'。
     检索时 WHERE subject=$1 会把两者当两个学科，demo 永远搜不到（或反过来污染真题）。
  2) 知识点：真题的 knowledge_point 存的是 DAG id（如 MA-B1-C1-S3-K02），
     RAG 直接返回给下游是机器码，得拿 csv 反查才知道叫什么名字。

做法（不改动 course_kb，规范表继续存 id 供掌握度归因）：
  - subject 'math' -> '数学'；
  - 新增 kp_code 列保存 DAG id（供 join kb_knowledge_points）；
  - knowledge_point 列回填为中文名（RAG 返回可读，评测也能直接比对）。

用法（137 的 agent-backend 容器内）：
    docker exec -w /app edu-agent-backend python /app/scripts/normalize_subject_kp.py /tmp/kp_id_to_name.csv
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from typing import Dict

import asyncpg


def load_mapping(path: str) -> Dict[str, str]:
    # csv 带 BOM，必须 utf-8-sig
    m: Dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("id") and row.get("name"):
                m[row["id"].strip()] = row["name"].strip()
    return m


async def main(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        raise SystemExit(f"找不到知识点映射表：{csv_path}")

    mapping = load_mapping(csv_path)
    print(f"载入知识点映射 {len(mapping)} 条")

    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        await conn.execute(
            "ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS kp_code varchar(32)"
        )

        # 1) subject 口径统一
        for t in ("course_kb", "course_kb_embeddings"):
            res = await conn.execute(f"UPDATE {t} SET subject='数学' WHERE subject='math'")
            n = int(res.split()[-1]) if res and res.startswith("UPDATE") else 0
            print(f"{t}: subject 'math'->'数学' 更新 {n} 行")

        # 2) 知识点 id -> 中文名，id 另存 kp_code
        rows = await conn.fetch(
            "select id, knowledge_point from course_kb_embeddings "
            "where subject='数学' and knowledge_point like 'MA-%'"
        )
        print(f"待回填中文名的题：{len(rows)}")

        updates, unmapped = [], 0
        for r in rows:
            name = mapping.get(r["knowledge_point"])
            if not name:
                unmapped += 1
                continue
            updates.append((name, r["knowledge_point"], r["id"]))
        await conn.executemany(
            "UPDATE course_kb_embeddings SET knowledge_point=$1, kp_code=$2 WHERE id=$3",
            updates,
        )
        print(f"回填成功 {len(updates)}，未命中映射 {unmapped}")

        print("\n--- course_kb_embeddings subject 分布 ---")
        for r in await conn.fetch(
            "select subject, count(*) n from course_kb_embeddings group by 1 order by 1"
        ):
            print(f"   {r['subject']:6} {r['n']} 行")

        print("--- 知识点样例（数学前 3 条）---")
        for r in await conn.fetch(
            "select knowledge_point, kp_code from course_kb_embeddings "
            "where subject='数学' and kp_code is not null limit 3"
        ):
            print(f"   {r['knowledge_point']}  <- {r['kp_code']}")

        miss = await conn.fetchval(
            "select count(*) from course_kb_embeddings where kp_code is null and subject='数学'"
        )
        print(f"数学中无 kp_code 的行（demo 种子题）: {miss}")
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("用法: normalize_subject_kp.py <kp_id_to_name.csv>")
    asyncio.run(main(sys.argv[1]))
