"""第一闸的 embedding 语义补盲：pgvector 余弦 TopK + 复合过滤。

为什么需要它：``text_fingerprint`` 只抓「标点/OCR 格式差异的同题」，抓不住
「同一道题被改写成另一种说法」。本模块用 ``course_kb.embedding``（1024 维，
与题库双写链路同源）做近似召回，再由纯函数叠加两道保险：

1. **数值签名一致**——题干数字集合不同即视为变式（换数题是 T-CAP2 的正常产物，
   不是重复题）；
2. **标答等价**——答案不同即视为异题。

137 真库 211 条向量实测：异题余弦最高 0.895，改写式同题 0.934；默认阈值
0.90 + 上述双过滤，存量 0 误报。

红线（与 T-CAP4 其余闸一致）：
* 语义命中只 ``manual_review`` 打标，**绝不自动隔离**——删/留由人裁决；
* embedding 端点不可用 / 维度不符 / 查询失败一律降级为「本次不做语义闸」，
  绝不阻塞题目入库，也不污染其它闸的裁定。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

from app.core.intake_guard import (
    DEFAULT_SEMANTIC_THRESHOLD,
    TAG_SEMANTIC,
    semantic_dup_check,
)
from app.services import embed as embed_svc

log = logging.getLogger(__name__)


def _flag(name: str, default: bool = True) -> bool:
    return os.environ.get(name, "true" if default else "false").strip().lower() not in (
        "0", "false", "no", "off",
    )


#: 总开关：线上出问题可只关语义闸，其余两闸与打标/隔离照常（无需回滚镜像）
SEMANTIC_ENABLED = _flag("DEDUP_SEMANTIC_ENABLED", True)
#: 余弦阈值，环境变量可覆盖（不进 git）
THRESHOLD = float(os.environ.get("DEDUP_SEMANTIC_SIMILARITY", str(DEFAULT_SEMANTIC_THRESHOLD)))
#: 每题召回几条候选：TopK 足够，全表两两比对在题库上万后会炸
TOP_K = max(1, int(os.environ.get("DEDUP_SEMANTIC_TOPK", "5")))


def vector_literal(vec: Sequence[float]) -> str:
    """pgvector 文本字面量；只由 float 拼装，不接受外部字符串。"""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


def vector_ok(vec: Any) -> bool:
    if not isinstance(vec, (list, tuple)) or len(vec) != embed_svc.EMBED_DIM:
        return False
    return all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in vec)


async def embed_rows(
    rows: Sequence[Any], *, db=None, tenant_id: Optional[str] = None,
    transport=None, config: Optional[dict] = None,
) -> List[Optional[List[float]]]:
    """把待入库题向量化；任何失败返回全 None（=本次跳过语义闸）。"""
    if not rows:
        return []
    contents = [
        embed_svc.build_content(
            str(getattr(r, "stem", "") or ""),
            str(getattr(r, "analysis", "") or ""),
        )
        for r in rows
    ]
    try:
        outcome = await embed_svc.embed_texts(
            contents, db=db, tenant_id=tenant_id,
            transport=transport, config=config,
        )
    except Exception as e:  # noqa: BLE001 embed_texts 自身已兜底，这里再兜一层
        log.warning("语义闸向量化异常，跳过语义闸：%s", e)
        return [None] * len(rows)
    if not outcome.get("ok"):
        log.warning("语义闸向量化降级，跳过语义闸：%s", outcome.get("reason", ""))
        return [None] * len(rows)
    vectors = outcome.get("vectors") or []
    if len(vectors) != len(rows):
        log.warning("语义闸向量条数不符 %s/%s，跳过", len(vectors), len(rows))
        return [None] * len(rows)
    return [v if vector_ok(v) else None for v in vectors]


def _sql(tenant: bool, *, asyncpg_style: bool):
    """TopK 最近邻；cast 到 vector 触发 pgvector 的 HNSW/索引。"""
    vec = "$1::vector" if asyncpg_style else "CAST(:vec AS vector)"
    self_id = "$2" if asyncpg_style else ":self_id"
    tenant_sql = (
        "AND tenant_id = $3" if asyncpg_style else "AND tenant_id = :tenant_id"
    ) if tenant else ""
    limit_ph = f"${4 if tenant else 3}" if asyncpg_style else ":k"
    return f"""
        SELECT id, stem, answer, type, 1 - (embedding <=> {vec}) AS sim
        FROM course_kb
        WHERE embedding IS NOT NULL AND id <> {self_id} {tenant_sql}
        ORDER BY embedding <=> {vec}
        LIMIT {limit_ph}
    """


async def collect_semantic_matches(
    db,
    rows: Sequence[Any],
    *,
    vectors: Optional[Sequence[Optional[List[float]]]] = None,
    scope_tenant_id: Optional[str] = None,
    threshold: Optional[float] = None,
    enabled: Optional[bool] = None,
    embed_db=None,
    embed_tenant_id: Optional[str] = None,
    transport=None,
    embed_config: Optional[dict] = None,
    persist_vectors: bool = True,
) -> Dict[int, Dict[str, Any]]:
    """返回 ``{row_id: semantic_dup_check 结果}``；失败/未配置一律返回空 dict。

    ``vectors`` 可由调用方预生成（避免与录题双写重复调用 embedding API）；
    ``scope_tenant_id`` 与文本指纹闸一致：采集全库、人工链路按租户。

    ``persist_vectors`` 默认 True：把本次生成的向量回填 ``course_kb.embedding``。
    这一步不能省——教师/采集/导入链路原先只写 RAG 表的 ``course_kb_embeddings``，
    ``course_kb.embedding`` 一直是 NULL，新题将永远无法成为后续语义闸的召回对象。
    回填只改 ORM 对象的字段，由调用方事务统一提交；不额外发 SQL。
    """
    if enabled is False or not SEMANTIC_ENABLED or not rows:
        return {}
    if vectors is None:
        vectors = await embed_rows(
            rows, db=embed_db if embed_db is not None else db,
            tenant_id=embed_tenant_id if embed_tenant_id is not None else scope_tenant_id,
            transport=transport, config=embed_config,
        )
    thr = float(THRESHOLD if threshold is None else threshold)
    stmt = text(_sql(bool(scope_tenant_id), asyncpg_style=False))
    matches: Dict[int, Dict[str, Any]] = {}
    for row, vec in zip(rows, vectors):
        if not vector_ok(vec):
            continue
        if persist_vectors and getattr(row, "embedding", None) is None:
            row.embedding = vec
        params: Dict[str, Any] = {
            "vec": vector_literal(vec), "self_id": row.id, "k": TOP_K,
        }
        if scope_tenant_id:
            params["tenant_id"] = scope_tenant_id
        try:
            found = (await db.execute(stmt, params)).all()
        except Exception as e:  # noqa: BLE001 语义闸只补盲，不能拖垮入库
            log.warning("语义闸检索失败（跳过本题）id=%s: %s", row.id, e)
            continue
        for other_id, stem, answer, qtype, sim in found:
            hit = semantic_dup_check(
                {
                    "stem": row.stem, "answer": row.answer,
                    "type": row.type, "options": getattr(row, "options", None),
                },
                match={
                    "similarity": sim, "threshold": thr,
                    "other": {
                        "id": other_id, "stem": stem,
                        "answer": answer, "type": qtype,
                    },
                },
            )
            if hit:
                matches[row.id] = hit
                break  # 只取最像的一条，供异常队列 dup_of 指向
    if matches:
        log.info("[guard:semantic] 语义近似 %s 题：%s", len(matches), sorted(matches))
    return matches


async def collect_semantic_matches_asyncpg(
    conn,
    rows: Sequence[dict],
    *,
    vectors: Optional[Sequence[Optional[List[float]]]] = None,
    scope_tenant_id: Optional[str] = None,
    threshold: Optional[float] = None,
) -> Dict[int, Dict[str, Any]]:
    """存量脚本版（asyncpg 直连），纯函数与过滤口径与运行时版完全一致。"""
    if not SEMANTIC_ENABLED or not rows:
        return {}
    if vectors is None:
        vectors = await embed_rows(rows)
    thr = float(THRESHOLD if threshold is None else threshold)
    sql = _sql(bool(scope_tenant_id), asyncpg_style=True)
    out: Dict[int, Dict[str, Any]] = {}
    for row, vec in zip(rows, vectors):
        if not vector_ok(vec):
            continue
        params: List[Any] = [vector_literal(vec), row["id"]]
        if scope_tenant_id:
            params.append(scope_tenant_id)
        params.append(TOP_K)
        try:
            found = await conn.fetch(sql, *params)
        except Exception as e:  # noqa: BLE001
            log.warning("存量语义检索失败（跳过本题）id=%s: %s", row["id"], e)
            continue
        for r in found:
            hit = semantic_dup_check(
                row,
                match={
                    "similarity": r["sim"], "threshold": thr,
                    "other": {
                        "id": r["id"], "stem": r["stem"],
                        "answer": r["answer"], "type": r["type"],
                    },
                },
            )
            if hit:
                out[row["id"]] = hit
                break
    return out


__all__ = [
    "SEMANTIC_ENABLED", "THRESHOLD", "TOP_K", "TAG_SEMANTIC",
    "collect_semantic_matches", "collect_semantic_matches_asyncpg",
    "embed_rows", "vector_literal", "vector_ok",
]
