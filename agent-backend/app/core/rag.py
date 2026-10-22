"""RAG 检索：真实模式查 106 的 pgvector；dev 模式用内存向量库（种子题库）。

统一返回 {"chunks": [{"text","score","knowledge_point","difficulty","answer"}], "points": [知识点]}。
真实模式依赖 asyncpg+pgvector（延迟导入）；dev 模式仅用标准库。
"""
from __future__ import annotations

import json
import math
import os

from app.core import config, embeddings

_DOCS: list[dict] = []
_VEC: list[list[float]] = []
_LOADED = False


def _load_seed() -> None:
    """加载种子题库并本地向量化，供 dev 模式检索（幂等）。"""
    global _DOCS, _VEC, _LOADED
    if _LOADED:
        return
    _LOADED = True
    path = config.SEED_PATH
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            _DOCS = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    _VEC = [embeddings.local_embed_one(_doc_text(d)) for d in _DOCS]


def _doc_text(d: dict) -> str:
    return d.get("content") or d.get("question", "")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return s / (na * nb)


def _fmt(score: float, doc: dict) -> dict:
    return {
        "text": _doc_text(doc),
        "score": round(float(score), 4),
        "knowledge_point": doc.get("knowledge_point", ""),
        "difficulty": doc.get("difficulty", ""),
        "answer": doc.get("answer", ""),
    }


async def retrieve(query: str, subject: str | None = None) -> dict:
    """检索与问题相关的知识点/例题。"""
    q_vec = await embeddings.embed_one(query)
    if config.db_ready():
        chunks = await _search_pg(q_vec, subject)
    else:
        chunks = _search_memory(q_vec, subject)
    points = list(dict.fromkeys(c["knowledge_point"] for c in chunks if c.get("knowledge_point")))
    return {"chunks": chunks, "points": points}


def _search_memory(q_vec: list[float], subject: str | None) -> list[dict]:
    _load_seed()
    if not _DOCS:
        return []
    scored: list[tuple[float, dict]] = []
    for doc, vec in zip(_DOCS, _VEC):
        if subject and doc.get("subject") and doc["subject"] != subject:
            continue
        scored.append((_cosine(q_vec, vec), doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_fmt(s, d) for s, d in scored[: config.TOP_K]]


async def _search_pg(q_vec: list[float], subject: str | None) -> list[dict]:
    from app.db.pg import get_pool  # 延迟导入

    pool = await get_pool()
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in q_vec) + "]"
    sql = """
        SELECT content, knowledge_point, difficulty, answer,
               1 - (embedding <=> $1::vector) AS score
        FROM course_kb_embeddings
        WHERE ($2::text IS NULL OR subject = $2)
        ORDER BY embedding <=> $1::vector
        LIMIT $3
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, vec_literal, subject, config.TOP_K)
    return [
        {
            "text": r["content"],
            "score": round(float(r["score"]), 4),
            "knowledge_point": r["knowledge_point"],
            "difficulty": r["difficulty"],
            "answer": r["answer"],
        }
        for r in rows
    ]


async def warm() -> None:
    """启动预热：dev 加载内存库；真实模式建立 pg 连接池。"""
    _load_seed()
    if config.db_ready():
        from app.db.pg import get_pool

        await get_pool()
