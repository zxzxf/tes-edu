"""灌种子题库：读 data/seed_questions.json，向量化后写入 106 的 pgvector。

用法（需先建表 sql/embeddings.sql，并配置 DB_DSN 与 EMBED_*）：
    export DB_DSN=postgresql://user:pass@106-host:5432/dbname
    export EMBED_BASE_URL=... EMBED_API_KEY=... EMBED_MODEL=text-embedding-v3
    python scripts/seed.py

未配置 EMBED_* 时用本地 dev 向量写入（仅用于把链路跑通，语义质量有限）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config, embeddings  # noqa: E402
from app.db import pg  # noqa: E402


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def main() -> None:
    if not config.db_ready():
        raise SystemExit("未配置 DB_DSN，无法灌库；dev 模式可跳过灌库直接用内存库检索。")

    with open(config.SEED_PATH, "r", encoding="utf-8") as f:
        docs = json.load(f)

    texts = [d.get("content") or d.get("question", "") for d in docs]
    vecs = await embeddings.embed(texts)

    pool = await pg.get_pool()
    async with pool.acquire() as conn:
        for d, v in zip(docs, vecs):
            await conn.execute(
                """
                INSERT INTO course_kb_embeddings
                    (subject, knowledge_point, difficulty, question, answer, content, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7::vector)
                """,
                d.get("subject", "math"),
                d.get("knowledge_point", ""),
                d.get("difficulty", ""),
                d.get("question", ""),
                d.get("answer", ""),
                d.get("content") or d.get("question", ""),
                _vec_literal(v),
            )
    print(f"seeded {len(docs)} docs into course_kb_embeddings")


if __name__ == "__main__":
    asyncio.run(main())
