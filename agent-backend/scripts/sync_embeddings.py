"""把 course_kb（Web 后端规范题库）的题同步进 RAG 检索表 course_kb_embeddings。

为什么需要这个脚本：
  import_bank.py 一次性写两张表，但两张表的 difficulty 列类型不同
  （course_kb.difficulty 为 integer，course_kb_embeddings.difficulty 为 text），
  直接灌会在第二张表报 `expected str, got int` —— 此时第一张表已写入成功，
  两张表会不一致。本脚本用于补齐/修复第二张表。

设计要点：
  1) 向量直接复制 course_kb.embedding，不重复调 embedding API（同源且省钱）；
  2) difficulty 转 str，匹配 text 列；
  3) content 与 import_bank._content_for_embed 保持一致（题干 + 解析/答案），
     否则检索返回的文本与生成向量时的文本不匹配；
  4) knowledge_point 取 knowledge_points[0]（DAG 知识点 id，非整个 JSON 数组）；
  5) 幂等：ON CONFLICT (content_hash) DO NOTHING，可安全重跑。

用法（在 137 的 agent-backend 容器内，因其有 DB_DSN 与 EMBED_*）：
    docker exec -w /app edu-agent-backend python /app/scripts/sync_embeddings.py

注意：106 的 pg_hba 只放行 137，本机直连不通；灌库必须在 137 容器内执行。
"""
from __future__ import annotations

import asyncio
import json
import os

import asyncpg


def content_for_embed(stem: str, analysis: str, answer: str) -> str:
    """与 import_bank._content_for_embed 保持一致。"""
    body = stem or ""
    if analysis:
        body += "\n解析：" + analysis
    elif answer:
        body += "\n答案：" + answer
    return body


def vec_literal(raw) -> str:
    return "[" + ",".join(
        "%.6f" % v for v in [float(x) for x in str(raw).strip("[]").split(",")]
    ) + "]"


def first_kp(kps) -> str:
    """取主知识点 id。asyncpg 取 jsonb 可能返回 str，需先解析。"""
    if isinstance(kps, str):
        try:
            kps = json.loads(kps)
        except (ValueError, TypeError):
            return kps
    return kps[0] if kps else ""


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DB_DSN"])
    try:
        await conn.execute(
            "ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS content_hash text"
        )
        # T-C3 题型路由：type 列（老库补齐 + 回填，与 add_qtype_to_embeddings.sql 同口径）
        await conn.execute(
            "ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "UPDATE course_kb_embeddings e SET type = c.type FROM course_kb c "
            "WHERE e.content_hash = c.content_hash AND (e.type IS NULL OR e.type = '')"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_emb_hash "
            "ON course_kb_embeddings(content_hash)"
        )

        rows = await conn.fetch(
            "select subject, chapter, stem, answer, analysis, difficulty, type, "
            "knowledge_points, content_hash, embedding from course_kb order by id"
        )
        ins = skip = 0
        for r in rows:
            kp = first_kp(r["knowledge_points"]) or (r["chapter"] or "")
            content = content_for_embed(r["stem"], r["analysis"], r["answer"])
            res = await conn.execute(
                """
                INSERT INTO course_kb_embeddings
                    (subject, knowledge_point, difficulty, question, answer, content, embedding, content_hash, type)
                VALUES ($1,$2,$3,$4,$5,$6,$7::vector,$8,$9)
                ON CONFLICT (content_hash) DO NOTHING
                """,
                r["subject"],
                kp,
                str(r["difficulty"]),          # text 列，必须转字符串
                r["stem"],
                r["answer"],
                content,
                vec_literal(r["embedding"]),
                r["content_hash"],
                r["type"] or "",               # T-C3 题型路由燃料
            )
            # 注意：ON CONFLICT DO NOTHING 冲突时 asyncpg 返回 "INSERT 0 0"（不是空串），
            # 只判断 startswith("INSERT") 会把跳过的行误计为新增。必须看末尾的影响行数。
            affected = int(res.split()[-1]) if res and res.startswith("INSERT") else 0
            if affected:
                ins += 1
            else:
                skip += 1

        print(f"补写：新增 {ins}，跳过(已存在) {skip}")
        print("course_kb            =", await conn.fetchval("select count(*) from course_kb"))
        print("course_kb_embeddings =", await conn.fetchval("select count(*) from course_kb_embeddings"))
        dist = await conn.fetch("select subject, count(*) n from course_kb_embeddings group by 1")
        print("embeddings subject 分布:", [dict(x) for x in dist])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
