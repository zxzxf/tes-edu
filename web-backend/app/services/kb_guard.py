"""T-CAP4 入库三闸的运行时接线（三条入库链路共用一份实现）。

纯函数（指纹/自洽/双源/合并裁定）在 ``app/core/intake_guard.py``；本模块只负责
「查题库索引 → 逐题裁定 → 写回 status/source_tag」，不重复任何判定逻辑。

三条链路与差异化口径：

======  ==========================  ==============  ===========  ================
链路    base_tag                    manual 落 tag   去重索引范围  开关
======  ==========================  ==============  ===========  ================
采集    ``auto_crawl``              **是**          全库          CRAWL_INTAKE_GUARD_ENABLED
教师录题 ``teacher``                否              本租户        TEACHER_INTAKE_GUARD_ENABLED
批量导入 ``import``                  否              本租户        IMPORT_INTAKE_GUARD_ENABLED
======  ==========================  ==============  ===========  ================

为什么 manual 落 tag 分链路：采集题的来源是非人（抽取），「未自动验证」是有效
信号；教师录题/Excel 导入本身就是人工来源且每题强制人审，给全库打
``*:guard_manual_review`` 只会制造噪音并冲掉来源语义（与用户 2026-11-17 决策一致）。

为什么去重索引分范围：机器采集要防「同一题从不同站被抓两次」，跨租户也算重复；
人工链路的题库是各校自己的，别校有同一道题不代表本校重复，按租户收窄避免误杀。

裁定写回统一为：明确错误 → ``status='quarantine'`` + ``<base_tag>:guard_quarantine``；
``manual_review`` 按上表决定是否打标；``pass`` 一律不动。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import select

from app.core.intake_guard import (
    TAG_MANUAL, TAG_QUARANTINE,
    VERDICT_MANUAL, VERDICT_PASS, VERDICT_QUARANTINE,
    judge_item, text_fingerprint, verdict_to_fields,
)
from app.models.course_kb import CourseQuestion

log = logging.getLogger(__name__)

CHAIN_CRAWL = "crawl"
CHAIN_TEACHER = "teacher"
CHAIN_IMPORT = "import"

_SWITCH_ENV = {
    CHAIN_CRAWL: "CRAWL_INTAKE_GUARD_ENABLED",
    CHAIN_TEACHER: "TEACHER_INTAKE_GUARD_ENABLED",
    CHAIN_IMPORT: "IMPORT_INTAKE_GUARD_ENABLED",
}


def _flag(env_name: str) -> bool:
    """开关默认开；显式置 0/false/no/off 才关（便于线上出问题一键回滚）。"""
    return os.environ.get(env_name, "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


#: 模块级开关，便于运维按链路单独关停（改 env 需重启进程，与既有 CRAWL 开关一致）
CRAWL_GUARD_ENABLED = _flag(_SWITCH_ENV[CHAIN_CRAWL])
TEACHER_GUARD_ENABLED = _flag(_SWITCH_ENV[CHAIN_TEACHER])
IMPORT_GUARD_ENABLED = _flag(_SWITCH_ENV[CHAIN_IMPORT])


def chain_enabled(chain: str) -> bool:
    if chain == CHAIN_CRAWL:
        return CRAWL_GUARD_ENABLED
    if chain == CHAIN_TEACHER:
        return TEACHER_GUARD_ENABLED
    if chain == CHAIN_IMPORT:
        return IMPORT_GUARD_ENABLED
    return True


def empty_stats(
    enabled: bool, scanned: int = 0, chain: str = "",
) -> Dict[str, Any]:
    return {
        "chain": chain, "enabled": enabled, "scanned": scanned,
        "pass": 0, "manual_review": 0, "quarantine": 0,
        "by_gate": {}, "samples": [],
    }


async def _build_index(
    db, scope_tenant_id: Optional[str]
) -> tuple[Dict[str, Set[int]], Dict[str, Set[int]]]:
    """题库倒排索引：content_hash → ids，题干指纹 → ids。

    ``scope_tenant_id`` 非空时只取该租户的行（人工链路用），None 取全库（采集链路用）。
    只取三列，避免把整行 JSON/向量拉回内存。
    """
    stmt = select(
        CourseQuestion.id, CourseQuestion.content_hash, CourseQuestion.stem,
    )
    if scope_tenant_id:
        stmt = stmt.where(CourseQuestion.tenant_id == scope_tenant_id)
    hash_index: Dict[str, Set[int]] = {}
    fp_index: Dict[str, Set[int]] = {}
    for qid, ch, stem in (await db.execute(stmt)).all():
        if ch:
            hash_index.setdefault(str(ch), set()).add(qid)
        fp = text_fingerprint(stem)
        if fp:
            fp_index.setdefault(fp, set()).add(qid)
    return hash_index, fp_index


async def apply_entry_guard(
    db,
    rows: Sequence[CourseQuestion],
    *,
    base_tag: str,
    chain: str,
    scope_tenant_id: Optional[str] = None,
    tag_manual: bool = False,
    secondary_answers: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """对刚入库的题跑三闸并按链路口径写回。

    :param rows: 已 flush/insert（有 id）的 ORM 行，就地改写 ``status``/``source_tag``；
    :param base_tag: 来源前缀，隔离时拼成 ``<base_tag>:guard_quarantine``；
    :param chain: 链路名（仅用于统计与日志）；
    :param scope_tenant_id: 去重索引收窄到该租户（人工链路必传）；
    :param tag_manual: ``manual_review`` 是否落 tag（采集 True，人工链路 False）；
    :param secondary_answers: 可选 ``{row_id: 第二来源答案}``，有才跑双源闸。

    批内同一指纹时**先到先得**：排在后面的题把前面的题视为已知重复，避免
    「一批里两条同题互相判重、双双隔离」把好数据也清掉。
    """
    stats = empty_stats(True, len(rows), chain)
    if not rows:
        return stats

    hash_index, fp_index = await _build_index(db, scope_tenant_id)
    ids = [obj.id for obj in rows]

    for pos, obj in enumerate(rows):
        later = set(ids[pos + 1:])  # 后面的题还没裁定，不算「已知」
        ch = str(obj.content_hash or "").strip()
        fp = text_fingerprint(obj.stem)
        known_hashes = (
            {ch} if ch and any(
                i != obj.id and i not in later for i in hash_index.get(ch, ())
            ) else set()
        )
        known_fps = (
            {fp} if fp and any(
                i != obj.id and i not in later for i in fp_index.get(fp, ())
            ) else set()
        )

        verdict = judge_item(
            {
                "content_hash": ch, "stem": obj.stem, "type": obj.type,
                "answer": obj.answer, "options": obj.options,
            },
            known_hashes=known_hashes, known_fps=known_fps,
            secondary_answer=(secondary_answers or {}).get(obj.id),
        )
        v = verdict["verdict"]
        g = verdict["gate"]
        stats[v] = stats.get(v, 0) + 1
        stats["by_gate"][g] = stats["by_gate"].get(g, 0) + 1

        fields = verdict_to_fields(v, base_tag=base_tag, tag_manual=tag_manual)
        if fields:
            for key, value in fields.items():
                setattr(obj, key, value)
        if v == VERDICT_QUARANTINE and len(stats["samples"]) < 5:
            stats["samples"].append(
                {"id": obj.id, "gate": g, "reason": verdict.get("reason", "")}
            )

    if stats["quarantine"]:
        log.info(
            "[guard:%s] 隔离 %s/%s 题，按闸分布 %s",
            chain, stats["quarantine"], stats["scanned"], stats["by_gate"],
        )
    return stats


def base_tag_of(rows: Sequence[Any], default: str) -> str:
    """取本批题的来源前缀（去掉 ``:guard_*`` 后缀），用于隔离后拼 tag。"""
    for obj in rows:
        tag = str(getattr(obj, "source_tag", "") or "").strip()
        if tag:
            return tag.split(":", 1)[0]
    return default


def rows_tenant_scope(rows: Sequence[Any]) -> Optional[str]:
    """单租户批次返回该租户；混租户（极少见）返回 None 表示按全库判定。"""
    tenants = {
        str(getattr(obj, "tenant_id", "") or "").strip() for obj in rows
    }
    tenants.discard("")
    return tenants.pop() if len(tenants) == 1 else None


__all__ = [
    "CHAIN_CRAWL", "CHAIN_TEACHER", "CHAIN_IMPORT",
    "CRAWL_GUARD_ENABLED", "TEACHER_GUARD_ENABLED", "IMPORT_GUARD_ENABLED",
    "TAG_MANUAL", "TAG_QUARANTINE",
    "VERDICT_MANUAL", "VERDICT_PASS", "VERDICT_QUARANTINE",
    "apply_entry_guard", "base_tag_of", "chain_enabled", "empty_stats",
    "rows_tenant_scope",
]
