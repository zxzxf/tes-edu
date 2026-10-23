"""把高中数学知识点 DAG（435 点 + 前置边）灌进 kb_knowledge_points。

前置条件：先执行扩表 SQL（知识点本体/kb_kp_extend.sql），加 code/chapter/section/
importance/book 列并建 kb_kp_prerequisites 边表。本脚本会自动执行该 SQL（幂等）。

数据来源（由 知识点本体/build_knowledge_dag.py / export_kp_for_import.py 生成）：
  - knowledge_points_数学_435_待入库.json ：435 个知识点
  - kb_kp_prerequisites_待入库.json      ：前置关系边（DAG）

幂等：知识点按 code 唯一索引冲突跳过；边按 (kp_code, prereq_code) 主键冲突跳过。

用法（137 的 agent-backend 容器内）：
    docker exec -w /app edu-agent-backend python /app/scripts/import_knowledge_dag.py \
        --sql /tmp/kb_kp_extend.sql \
        --kp  /tmp/knowledge_points_数学_435_待入库.json \
        --edge /tmp/kb_kp_prerequisites_待入库.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import asyncpg


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", required=True, help="扩表 SQL（kb_kp_extend.sql）")
    ap.add_argument("--kp", required=True, help="知识点 JSON")
    ap.add_argument("--edge", required=True, help="前置边 JSON")
    args = ap.parse_args()

    for p in (args.sql, args.kp, args.edge):
        if not os.path.exists(p):
            raise SystemExit(f"找不到文件：{p}")

    kps = json.load(open(args.kp, encoding="utf-8"))
    edges = json.load(open(args.edge, encoding="utf-8"))
    print(f"待灌知识点 {len(kps)}，前置边 {len(edges)}")

    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        # 1) 扩表（幂等）
        await conn.execute(open(args.sql, encoding="utf-8").read())
        print("扩表 SQL 执行完成")

        # 2) 灌知识点
        ins = skip = 0
        for k in kps:
            res = await conn.execute(
                """
                INSERT INTO kb_knowledge_points
                    (subject, grade, title, content, tags, code, chapter, section, importance, book)
                VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)
                ON CONFLICT (code) DO NOTHING
                """,
                k.get("subject"), k.get("grade"), k.get("title"), k.get("content"),
                json.dumps(k.get("tags") or []), k.get("code"), k.get("chapter"),
                k.get("section"), k.get("importance"), k.get("book"),
            )
            affected = int(res.split()[-1]) if res and res.startswith("INSERT") else 0
            ins += 1 if affected else 0
            skip += 0 if affected else 1
        print(f"知识点：新增 {ins}，跳过 {skip}")

        # 3) 灌前置边
        eins = eskip = 0
        for e in edges:
            res = await conn.execute(
                """
                INSERT INTO kb_kp_prerequisites (kp_code, prereq_code)
                VALUES ($1,$2)
                ON CONFLICT (kp_code, prereq_code) DO NOTHING
                """,
                e["kp_code"], e["prereq_code"],
            )
            affected = int(res.split()[-1]) if res and res.startswith("INSERT") else 0
            eins += 1 if affected else 0
            eskip += 0 if affected else 1
        print(f"前置边：新增 {eins}，跳过 {eskip}")

        # 4) 校验
        n = await conn.fetchval("select count(*) from kb_knowledge_points")
        ne = await conn.fetchval("select count(*) from kb_kp_prerequisites")
        print(f"\nkb_knowledge_points = {n} 行；kb_kp_prerequisites = {ne} 行")

        dangling = await conn.fetch(
            """
            select p.kp_code, p.prereq_code from kb_kp_prerequisites p
            left join kb_knowledge_points k on k.code = p.prereq_code
            where k.code is null
            """
        )
        print("悬空前置引用:", [dict(x) for x in dangling] or "无")

        # 环检测（DAG 前提）
        rows = await conn.fetch("select code from kb_knowledge_points")
        edges_all = await conn.fetch("select kp_code, prereq_code from kb_kp_prerequisites")
        indeg = {r["code"]: 0 for r in rows}
        adj: dict[str, list[str]] = {r["code"]: [] for r in rows}
        for e in edges_all:
            if e["kp_code"] in indeg and e["prereq_code"] in adj:
                indeg[e["kp_code"]] += 1
                adj[e["prereq_code"]].append(e["kp_code"])
        from collections import deque
        q = deque([c for c, d in indeg.items() if d == 0])
        seen = 0
        while q:
            cur = q.popleft()
            seen += 1
            for nxt in adj[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        print(f"拓扑排序覆盖 {seen}/{len(rows)} -> {'无环(真 DAG)' if seen == len(rows) else '存在环!'}")

        print("\n按册次分布:")
        for r in await conn.fetch(
            "select book, count(*) n from kb_knowledge_points group by 1 order by 1"
        ):
            print(f"   {r['book']}: {r['n']}")
        core = await conn.fetchval(
            "select count(*) from kb_knowledge_points where importance='core'"
        )
        print("高频考点(core):", core)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
