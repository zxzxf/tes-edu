"""RAG 检索：真实模式查 106 的 pgvector；dev 模式用内存向量库（种子题库）。

统一返回 {"chunks": [{"text","score","knowledge_point","difficulty","answer",
"content_hash","type"}], "points": [知识点]}。
T-C3 起 chunks 带 ``type``（course_kb.type 题型，供题型路由）——线上列由
``scripts/add_qtype_to_embeddings.sql`` 迁移补齐；列缺失时自动降级为不带
type 的查询（自愈，不影响检索主链路）。
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
_VEC_READY = False
#: course_kb_embeddings.type 列是否存在（T-C3 迁移前的老库为 False，查询自动降级）
_TYPE_COLUMN_OK = True


def _load_seed() -> None:
    """加载种子题库（仅文档，不含向量；向量由 _ensure_vectors 统一生成）。"""
    global _DOCS, _LOADED
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


async def _ensure_vectors() -> None:
    """生成内存库向量：embed 可用时走真实向量，否则本地回退。

    关键：必须与查询向量走同一套 embed，否则两者处于不同向量空间，相似度无意义。
    """
    global _VEC, _VEC_READY
    _load_seed()
    if _VEC_READY or not _DOCS:
        return
    _VEC = await embeddings.embed([_doc_text(d) for d in _DOCS])
    _VEC_READY = True


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
        # judge v2 靠它定位 course_kb_steps（步骤级归因燃料）
        "content_hash": doc.get("content_hash", ""),
        # T-C3 题型路由燃料（course_kb.type；dev 种子无该字段时为空串->保守中等）
        "type": doc.get("type", "") or "",
    }


async def retrieve(query: str, subject: str | None = None) -> dict:
    """检索与问题相关的知识点/例题。"""
    q_vec = await embeddings.embed_one(query)
    if config.db_ready():
        chunks = await _search_pg(q_vec, subject)
    else:
        await _ensure_vectors()
        chunks = _search_memory(q_vec, subject)
    points = list(dict.fromkeys(c["knowledge_point"] for c in chunks if c.get("knowledge_point")))
    return {"chunks": chunks, "points": points}


def _search_memory(q_vec: list[float], subject: str | None) -> list[dict]:
    if not _DOCS or not _VEC:
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

    global _TYPE_COLUMN_OK
    pool = await get_pool()
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in q_vec) + "]"

    def _build_sql(with_type: bool) -> str:
        # T-C3：type 列随迁移补齐；老库缺列时降级为不带 type 的查询（自愈，不 500）
        type_field = "type," if with_type else ""
        return f"""
            SELECT content, knowledge_point, difficulty, answer, content_hash,
                   {type_field}
                   1 - (embedding <=> $1::vector) AS score
            FROM course_kb_embeddings
            WHERE ($2::text IS NULL OR subject = $2)
              -- 硬隔离 seed demo 假题：id 1-46 的编造例题不参与线上检索，
              -- 只保留供压测/回滚；物理删除前一律靠此谓词挡掉。
              AND (is_demo IS NOT TRUE)
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _build_sql(_TYPE_COLUMN_OK), vec_literal, subject, config.TOP_K
            )
    except Exception as e:  # noqa: BLE001 缺 type 列（迁移未执行）-> 降级重试一次
        if not _TYPE_COLUMN_OK or "type" not in str(e):
            raise
        _TYPE_COLUMN_OK = False
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                _build_sql(False), vec_literal, subject, config.TOP_K
            )
    return [
        {
            "text": r["content"],
            "score": round(float(r["score"]), 4),
            "knowledge_point": r["knowledge_point"],
            "difficulty": r["difficulty"],
            "answer": r["answer"],
            "content_hash": r["content_hash"] or "",
            "type": (r["type"] if "type" in r else "") or "",
        }
        for r in rows
    ]


async def warm() -> None:
    """启动预热：连 pg 时建连接池；否则预生成内存库向量（避免首请求才现算）。"""
    _load_seed()
    if config.db_ready():
        from app.db.pg import get_pool

        await get_pool()
    else:
        await _ensure_vectors()
