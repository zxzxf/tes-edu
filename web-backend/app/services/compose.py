"""组卷 / 变式接口（T-A4）：按知识点从题库拉题、变式推荐、组卷配额。

基于 kb_kp_prerequisites DAG + course_kb.knowledge_points：
  - compose_variants(kp)：给定知识点 code，返回**该点及下游相关题**（变式）——
    同点变式（tier0）优先，直接下游（tier1）其次，间接下游（tier2）兜底；
  - compose_exam(kps, n)：按多个知识点组卷（每点均分配额 + 难度/题型过滤）。

设计原则同 adaptive.py / mastery.py：
  - 「怎么分层、怎么分配配额、怎么选题」是纯函数（不连库），无 DB 可单测；
  - DB 查询集中在薄层，动态 SQL 每个参数一个命名占位符（e IN (:kp0,:kp1...)）——
    避开 SQLAlchemy text() 的数组参数类型推断与「绑定参数后接 ::」解析坑
    （T-B3 实测教训），列上的 ::jsonb 安全。

与 T-B3 的关系：adaptive.recommend 的选课查询委托本模块 fetch_questions，
两侧共用同一套 course_kb 读取口径（计划书「与 T-B3 共用」要求）。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import mastery

# tier -> 组卷配额权重（变式优先同点与直接下游）
DEFAULT_TIER_WEIGHTS: Dict[int, float] = {0: 0.6, 1: 0.25, 2: 0.15}

# API/内部题型码 -> course_kb.type（中文，与 import_bank 落库口径一致）
TYPE_CODE_TO_CN: Dict[str, str] = {
    "single": "单选",
    "multi": "多选",
    "fill": "填空",
    "solve": "解答",
    "experiment": "实验",
}


def normalize_types(types: Optional[Sequence[str]]) -> List[str]:
    """题型入参归一化：中英文皆可，去重去空。"""
    out: List[str] = []
    for t in types or []:
        if not t:
            continue
        cn = TYPE_CODE_TO_CN.get(str(t).strip().lower(), str(t).strip())
        if cn not in out:
            out.append(cn)
    return out


# --------------------------------------------------------------------------- #
# 纯函数层（不连库，便于测试）
# --------------------------------------------------------------------------- #

def walk_down(edges: Sequence[Tuple[str, str]], node: str) -> Set[str]:
    """返回 node 的全部（传递性）下游知识点集合（依赖 node 的点），不含 node 自身。

    edges: list of (kp_code, prereq_code)（同 T-B2 口径，kp->prereq）。
    例：边 (B,A),(C,B) 表示 A 是 B 的前置；walk_down(A)={B,C}。
    环防御：seen 集合保证坏数据不死循环。
    """
    adj: Dict[str, List[str]] = {}
    for kp, pre in edges or []:
        adj.setdefault(pre, []).append(kp)
    seen: Set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    seen.discard(node)                                    # 契约：不含 node 自身（环数据也成立）
    return seen


def related_kps(
    edges: Sequence[Tuple[str, str]],
    node: str,
    max_tier: int = 2,
) -> Dict[str, int]:
    """node + 下游按 tier 分层：tier0=自身，tier1=直接下游，tier2=间接下游。

    返回 {kp_code: tier}。BFS 逐层展开，max_tier 截断；
    `if nxt not in out` 天然防环/防重。
    """
    if not node:
        return {}
    adj: Dict[str, List[str]] = {}
    for kp, pre in edges or []:
        adj.setdefault(pre, []).append(kp)
    out: Dict[str, int] = {node: 0}
    frontier = [node]
    tier = 0
    while frontier and tier < max_tier:
        nxt_frontier: List[str] = []
        for cur in frontier:
            for nxt in adj.get(cur, []):
                if nxt not in out:
                    out[nxt] = tier + 1
                    nxt_frontier.append(nxt)
        frontier = nxt_frontier
        tier += 1
    return out


def plan_composition(
    n: int,
    related: Dict[str, int],
    counts: Optional[Dict[str, int]] = None,
    weights: Optional[Dict[int, float]] = None,
) -> Dict[str, int]:
    """组卷配额：{kp: 题数}，总量收敛到 n。

    两轮分配：
      1. 保底轮：每个有题的 KP 至少 1 题（tier 升序，层内题量降序）；
      2. 余量轮：按 tier 权重 + 题量降序 round-robin 补足到 n
         （单个 KP 不超过其候选题量 counts）。
    """
    n = int(n or 0)
    counts = counts or {}
    weights = weights or DEFAULT_TIER_WEIGHTS
    pool = {kp: t for kp, t in (related or {}).items() if counts.get(kp, 0) > 0}
    if n <= 0 or not pool:
        return {}
    plan: Dict[str, int] = {kp: 0 for kp in pool}
    tiers = sorted(set(pool.values()))

    def _total() -> int:
        return sum(plan.values())

    # 保底轮
    for t in tiers:
        if _total() >= n:
            break
        for kp in sorted((k for k in pool if pool[k] == t),
                         key=lambda k: (-counts.get(k, 0), k)):
            if _total() >= n:
                break
            plan[kp] += 1
    # 余量轮：低 tier（同点变式）优先吃满——每轮只补当前最高优先级层的一点，
    # 层内在「配额最少」的点间轮转（题量降序破平），保证变式优先且多点覆盖
    weight_order = sorted(tiers, key=lambda t: -weights.get(t, 0.0))
    while _total() < n:
        progressed = False
        for t in weight_order:
            if _total() >= n:
                break
            kps = [k for k in pool if pool[k] == t and plan[k] < counts.get(k, 0)]
            if not kps:
                continue                                   # 该层已吃满，下沉一层
            kp = sorted(kps, key=lambda k: (plan[k], -counts.get(k, 0), k))[0]
            plan[kp] += 1
            progressed = True
            break                                          # 每轮只补一点，低 tier 优先
        if not progressed:
            break                                       # 全部 KP 已达题量上限
    return {k: v for k, v in plan.items() if v > 0}


def allocate_evenly(
    n: int,
    counts: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """组卷均分配额：每个 KP 至少 1 题，余量按题量降序 round-robin。

    与 plan_composition 的区别：无 tier 概念，多 KP 组卷场景（每点平等）。
    """
    n = int(n or 0)
    counts = counts or {}
    pool = {k: c for k, c in counts.items() if k and c > 0}
    if n <= 0 or not pool:
        return {}
    plan: Dict[str, int] = {k: 0 for k in pool}

    def _total() -> int:
        return sum(plan.values())

    for kp in sorted(pool, key=lambda k: (-pool[k], k)):
        if _total() >= n:
            break
        plan[kp] += 1
    while _total() < n:
        kps = [k for k in pool if plan[k] < pool[k]]
        if not kps:
            break
        kp = sorted(kps, key=lambda k: (-pool[k], k))[0]
        plan[kp] += 1
    return {k: v for k, v in plan.items() if v > 0}


def pick_by_plan(
    pool: Sequence[dict],
    plan: Dict[str, int],
    related: Optional[Dict[str, int]] = None,
    seen: Optional[Set[str]] = None,
) -> List[dict]:
    """从候选池按配额选题（纯函数）。

    - 每题按 (tier, kp) 顺序归属：tier 小的 KP 先填配额（变式优先同点）；
    - content_hash 去重（seen 可由调用方传入，实现跨调用去重）；
    - 返回项附带 kp_hit / tier，供前端解释「为什么选这题」。
    """
    related = related or {}
    seen = set(seen or set())
    used: Dict[str, int] = {}
    items: List[dict] = []
    for kp in sorted(plan, key=lambda k: (related.get(k, 9), k)):
        quota = plan.get(kp, 0)
        for q in pool or []:
            h = q.get("content_hash")
            if not h or h in seen:
                continue
            if used.get(kp, 0) >= quota:
                break
            if kp in (q.get("knowledge_points") or []):
                seen.add(h)
                used[kp] = used.get(kp, 0) + 1
                items.append({**q, "kp_hit": kp, "tier": related.get(kp, 9)})
    return items


# --------------------------------------------------------------------------- #
# DB 薄层
# --------------------------------------------------------------------------- #

def _question_select_sql(
    kp_count: int,
    type_count: int,
    need_difficulty: bool,
    exclude_count: int,
    need_subject: bool = False,
) -> str:
    """动态拼装选题 SQL（列上的 ::jsonb 安全；每参数一个命名占位符）。"""
    conds: List[str] = []
    params: List[str] = []
    for i in range(kp_count):
        params.append(f":kp{i}")
    kp_cond = " OR ".join(f"e = {p}" for p in params)
    conds.append(f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(knowledge_points::jsonb) AS e WHERE {kp_cond})")
    if need_subject:
        conds.append("subject = :s")
    if need_difficulty:
        conds.append("difficulty BETWEEN :lo AND :hi")
    if type_count:
        conds.append("type IN (" + ", ".join(f":t{i}" for i in range(type_count)) + ")")
    if exclude_count:
        conds.append("content_hash NOT IN (" + ", ".join(f":x{i}" for i in range(exclude_count)) + ")")
    return (
        "SELECT id, subject, type, stem, options, answer, analysis, "
        "difficulty, knowledge_points, content_hash FROM course_kb "
        "WHERE " + " AND ".join(conds) + " ORDER BY id LIMIT :lim"
    )


async def fetch_questions(
    db: AsyncSession,
    kps: Sequence[str],
    difficulty_lo: Optional[int] = None,
    difficulty_hi: Optional[int] = None,
    types: Optional[Sequence[str]] = None,
    limit: int = 200,
    exclude_hashes: Optional[Sequence[str]] = None,
    subject: Optional[str] = None,
) -> List[dict]:
    """按知识点 code 集合拉题（可选学科 / 难度区间 / 题型 / 排除已出题 hash）。"""
    kps = [k for k in (kps or []) if k]
    if not kps:
        return []
    types_cn = normalize_types(types)
    exclude = [h for h in (exclude_hashes or []) if h]
    need_difficulty = difficulty_lo is not None or difficulty_hi is not None
    sql = _question_select_sql(
        len(kps), len(types_cn), need_difficulty, len(exclude),
        need_subject=bool(subject),
    )
    bind: Dict[str, object] = {"lim": max(1, min(int(limit or 200), 1000))}
    if subject:
        bind["s"] = subject
    for i, kp in enumerate(kps):
        bind[f"kp{i}"] = kp
    if need_difficulty:
        bind["lo"] = int(difficulty_lo) if difficulty_lo is not None else 1
        bind["hi"] = int(difficulty_hi) if difficulty_hi is not None else 5
    for i, t in enumerate(types_cn):
        bind[f"t{i}"] = t
    for i, h in enumerate(exclude):
        bind[f"x{i}"] = h
    res = await db.execute(text(sql).bindparams(**bind))
    return [dict(r) for r in res.mappings()]


async def count_questions_by_kp(
    db: AsyncSession,
    kps: Sequence[str],
) -> Dict[str, int]:
    """每个 KP 的候选题目数（一条聚合 SQL，组卷配额用）。"""
    kps = [k for k in (kps or []) if k]
    if not kps:
        return {}
    in_list = ", ".join(f":kp{i}" for i in range(len(kps)))
    sql = (
        "SELECT e AS kp, count(*) AS n FROM course_kb, "
        f"jsonb_array_elements_text(knowledge_points::jsonb) AS e "
        f"WHERE knowledge_points IS NOT NULL AND e IN ({in_list}) GROUP BY e"
    )
    bind = {f"kp{i}": kp for i, kp in enumerate(kps)}
    res = await db.execute(text(sql).bindparams(**bind))
    return {r[0]: int(r[1]) for r in res.all() if r[0]}


async def load_related_with_counts(
    db: AsyncSession,
    kp: str,
    max_tier: int = 2,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """kp + 下游分层（related）及各点题量（counts）。"""
    if not kp:
        return {}, {}
    edges = await mastery.load_dag_edges(db)
    related = related_kps(edges, kp, max_tier)
    counts = await count_questions_by_kp(db, list(related))
    return related, counts


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #

async def compose_variants(
    db: AsyncSession,
    kp: str,
    n: int = 5,
    difficulty_lo: Optional[int] = None,
    difficulty_hi: Optional[int] = None,
    types: Optional[Sequence[str]] = None,
    exclude_hashes: Optional[Sequence[str]] = None,
    max_tier: int = 2,
) -> dict:
    """变式题：给定知识点 code，返回该点及下游相关题（tier0 同点变式优先）。

    返回 {kp, related, counts, plan, items, total}：
      - related: {kp_code: tier}（0=自身，1=直接下游，2=间接下游）
      - items: 每题带 kp_hit / tier / 难度，可解释「为什么推这题」
      - 不足 n 时如实返回现有题量（note 说明原因）
    """
    n = max(1, min(int(n or 5), 50))
    related, counts = await load_related_with_counts(db, kp, max_tier)
    if not counts:
        return {
            "kp": kp, "related": related, "counts": {}, "plan": {},
            "items": [], "total": 0,
            "note": "该知识点（及下游）暂无题目：可能题库未覆盖，或知识点标签未挂 DAG code",
        }
    plan = plan_composition(n, related, counts)
    pool = await fetch_questions(
        db, list(plan), difficulty_lo, difficulty_hi, types,
        limit=200, exclude_hashes=exclude_hashes,
    )
    items = pick_by_plan(pool, plan, related)
    return {
        "kp": kp, "related": related, "counts": counts, "plan": plan,
        "items": items, "total": len(items),
        "note": None if len(items) >= n else f"候选不足：仅组出 {len(items)}/{n} 题",
    }


async def compose_exam(
    db: AsyncSession,
    kps: Sequence[str],
    n: int = 10,
    difficulty_lo: Optional[int] = None,
    difficulty_hi: Optional[int] = None,
    types: Optional[Sequence[str]] = None,
    exclude_hashes: Optional[Sequence[str]] = None,
) -> dict:
    """组卷：按多个知识点均分配额拉题（难度/题型过滤，hash 去重）。"""
    kps = [k for k in (kps or []) if k]
    n = max(1, min(int(n or 10), 100))
    if not kps:
        return {"kps": [], "counts": {}, "items": [], "total": 0,
                "note": "未提供知识点 code"}
    counts = await count_questions_by_kp(db, kps)
    if not counts:
        return {"kps": kps, "counts": {}, "items": [], "total": 0,
                "note": "这些知识点暂无题目"}
    plan = allocate_evenly(n, counts)
    pool = await fetch_questions(
        db, list(plan), difficulty_lo, difficulty_hi, types,
        limit=200, exclude_hashes=exclude_hashes,
    )
    items = pick_by_plan(pool, plan)
    return {
        "kps": kps, "counts": counts, "items": items, "total": len(items),
        "note": None if len(items) >= n else f"候选不足：仅组出 {len(items)}/{n} 题",
    }
