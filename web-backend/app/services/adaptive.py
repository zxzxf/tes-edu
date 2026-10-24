"""ZPD 最近发展区 + 自适应推题引擎（T-B3）。

基于 T-B2 的掌握度图谱（student_mastery + kb_kp_prerequisites DAG）算最近发展区：
「前置已掌握、自身未掌握」的知识点才推题——
  - 不推已掌握的（做了无效，还浪费题）；
  - 不推前置没掌握的（超纲，挫败感）；
  - 冷启动（全无记录）时根节点全部就绪，形成学习路径起点。

设计原则同 mastery.py：
  - 「ZPD 怎么算、难度怎么定、复习怎么排」是纯函数（不连库），无 DB 可单测；
  - DB 读写集中在带 db 参数的薄层；与 T-B2 的 walk_up / 衰减完全复用。

与 T-B2 的一致性约定：掌握度缺省（无判分记录）按「已掌握/就绪」处理
（plan_mastery_updates 同款约定，避免冷启动全阻塞与误扣薄弱点）。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import compose
from app.services import mastery

# 阈值沿用 T-B2（与 reports.py 周报口径对齐）
MASTERED_THRESHOLD = mastery.MASTERED_THRESHOLD   # 0.8：>= 视为已掌握，不再推
WEAK_THRESHOLD = mastery.WEAK_THRESHOLD           # 0.6：< 视为薄弱（阻塞其后置）

# 难度带 -> course_kb.difficulty（1~5 整数）区间
BAND_DIFFICULTY: Dict[str, Tuple[int, int]] = {
    "easy": (1, 2),
    "medium": (3, 3),
    "hard": (4, 5),
}

# 选课查询已统一委托 app.services.compose.fetch_questions（T-A4 与 T-B3 共用口径）


# --------------------------------------------------------------------------- #
# 纯函数层（不连库，便于测试）
# --------------------------------------------------------------------------- #

def compute_zpd(
    mastery_map: Optional[Dict[str, float]],
    edges: Sequence[Tuple[str, str]],
    all_kps: Sequence[str],
    mastered_threshold: float = MASTERED_THRESHOLD,
) -> Set[str]:
    """最近发展区：前置全部就绪、自身未掌握的知识点集合。

    参数：
      mastery_map: {kp_code: 衰减后掌握度}，仅含已判分点（同 load_mastery_map 口径）
      edges: [(kp_code, prereq_code)]，kp->prereq 方向（同 T-B2）
      all_kps: 候选全集（kb_knowledge_points.code）

    判定：
      - 自身：无记录 或 < mastered_threshold -> 未掌握（进入候选）
      - 前置（全部祖先）：**有记录且 < mastered_threshold 才阻塞**；
        无记录按就绪处理（T-B2 约定，冷启动不全阻塞）
    """
    mmap = mastery_map or {}
    zpd: Set[str] = set()
    for kp in all_kps or []:
        cur = mmap.get(kp)
        if cur is not None and cur >= mastered_threshold:
            continue                                    # 已掌握：不推
        blocked = False
        for pre in mastery.walk_up(edges, kp):
            score = mmap.get(pre)
            if score is not None and score < mastered_threshold:
                blocked = True                          # 前置是已知薄弱点：超纲，不推
                break
        if not blocked:
            zpd.add(kp)
    return zpd


def depth_map(
    edges: Sequence[Tuple[str, str]],
    all_kps: Sequence[str],
) -> Dict[str, int]:
    """每个知识点的最长前置链深度（根=0）。用于学习路径排序：浅的先学。

    防御环：DAG 已经 T-B1 无环校验，这里仍做 stack 保护，坏数据不会死循环。
    """
    adj: Dict[str, List[str]] = {}
    for kp, pre in edges or []:
        adj.setdefault(kp, []).append(pre)
    memo: Dict[str, int] = {}

    def _depth(kp: str, stack: Tuple[str, ...]) -> int:
        if kp in memo:
            return memo[kp]
        if kp in stack:
            return 0
        best = 0
        for p in adj.get(kp, []):
            best = max(best, 1 + _depth(p, stack + (kp,)))
        memo[kp] = best
        return best

    return {kp: _depth(kp, ()) for kp in (all_kps or [])}


def difficulty_band(
    mastery: Optional[float],
    recent_rate: Optional[float] = None,
) -> str:
    """难度带 easy/medium/hard。

    - mastery: 衰减后掌握度（None=未学 -> easy）
    - recent_rate: 近期正确率 0~1（答对升档、答错降档），与掌握度各占一半权重；
      缺省时只看掌握度。长期看掌握度本身随答对升/答错降（T-B2 更新规则），
      recent_rate 是短期修正，供事件流/后续策略接入。
    """
    base = 0.0 if mastery is None else float(mastery)
    if recent_rate is not None:
        r = min(1.0, max(0.0, float(recent_rate)))
        base = 0.5 * base + 0.5 * r
    if base < 0.4:
        return "easy"
    if base < 0.7:
        return "medium"
    return "hard"


def band_to_difficulty(band: str) -> Tuple[int, int]:
    """难度带 -> course_kb.difficulty（1~5）闭区间。"""
    return BAND_DIFFICULTY.get(band, (3, 3))


def plan_review(
    rows: Sequence[dict],
    now: Optional[datetime] = None,
    half_life_days: float = mastery.DEFAULT_HALF_LIFE_DAYS,
    review_threshold: float = WEAK_THRESHOLD,
    skip_above: float = 0.95,
) -> List[dict]:
    """间隔重复计划：找出「曾掌握、正在遗忘」的知识点，按紧迫度升序。

    参数：
      rows: [{kp, correct_count, wrong_count, last_practiced_at}]
      review_threshold: 衰减到该值以下视为「该复习了」
      skip_above: 衰减后仍高于该值的跳过（刚练熟，排进来是噪声）

    返回项：{kp, raw, decayed, last_practiced_at, days_until_review, urgency}
      - urgency='due'：已忘到阈值以下，应立即复习
      - urgency='upcoming'：还在阈值上，给出倒计时天数
    排序：days_until_review 升序（最紧迫在前）。
    """
    now = now or datetime.now(timezone.utc)
    out: List[dict] = []
    for r in rows or []:
        kp = r.get("kp")
        if not kp:
            continue
        c = int(r.get("correct_count") or 0)
        w = int(r.get("wrong_count") or 0)
        judged = c + w
        if judged == 0:
            continue                                    # 只练过没判分：不构成遗忘
        raw = c / judged
        last = r.get("last_practiced_at")
        decayed = mastery.compute_mastery_score(c, w, last, now, half_life_days)
        if decayed is None:
            continue
        if raw < review_threshold:
            continue                                    # 从未真掌握：归 ZPD 推题，不算遗忘
        if decayed >= skip_above:
            continue                                    # 还很熟：无需排入
        if decayed < review_threshold:
            days_left, urgency = 0.0, "due"
        else:
            # 还要多少天衰减到阈值：t = 半衰期 * log2(当前/阈值)
            days_left = half_life_days * math.log2(decayed / review_threshold)
            urgency = "upcoming"
        out.append({
            "kp": kp,
            "raw": round(raw, 4),
            "decayed": round(decayed, 4),
            "last_practiced_at": last,
            "days_until_review": round(days_left, 1),
            "urgency": urgency,
        })
    out.sort(key=lambda x: (x["days_until_review"], x["kp"]))
    return out


# --------------------------------------------------------------------------- #
# DB 薄层
# --------------------------------------------------------------------------- #

async def load_all_kps(db: AsyncSession, subject: Optional[str] = None) -> List[str]:
    """全部知识点 code（DAG 节点全集），可按学科过滤。"""
    sql = "SELECT code FROM kb_knowledge_points"
    if subject:
        sql += " WHERE subject = :s"
    res = await db.execute(text(sql).bindparams(s=subject) if subject else text(sql))
    return [r[0] for r in res.all() if r[0]]


async def compute_zpd_db(
    db: AsyncSession,
    student_id: str,
    subject: Optional[str] = None,
) -> dict:
    """ZPD（DB 版）：返回 {student_id, subject, zpd, mastery}。

    zpd 按掌握度升序（越弱的越先推；未练过的排最前，视为 -1）。
    mastery 仅含 ZPD 内点的当前衰减掌握度（None=未练过）。
    """
    codes = await load_all_kps(db, subject)
    if not codes:
        return {"student_id": student_id, "subject": subject, "zpd": [], "mastery": {}}
    edges = await mastery.load_dag_edges(db)
    mmap = await mastery.load_mastery_map(db, student_id, codes)
    zpd = compute_zpd(mmap, edges, codes)
    dm = depth_map(edges, codes)

    def _order(k: str):
        s = mmap.get(k)
        # 已练过的薄弱点优先（补救），未练的按学习路径深度（浅的先学）
        return (0 if s is not None else 1, s if s is not None else 0.0, dm.get(k, 0), k)

    ordered = sorted(zpd, key=_order)
    return {
        "student_id": student_id,
        "subject": subject,
        "zpd": ordered,
        "mastery": {k: mmap.get(k) for k in ordered},
    }


async def review_plan_db(
    db: AsyncSession,
    student_id: str,
    subject: Optional[str] = None,
) -> List[dict]:
    """间隔重复计划（DB 版）：读 student_mastery 原始计数 -> plan_review。"""
    sql = (
        "SELECT knowledge_point AS kp, correct_count, wrong_count, last_practiced_at "
        "FROM student_mastery WHERE student_id = :sid"
    )
    if subject:
        sql += " AND subject = :s"
    res = await db.execute(
        text(sql).bindparams(sid=student_id, s=subject) if subject
        else text(sql).bindparams(sid=student_id)
    )
    rows = [
        {
            "kp": r[0],
            "correct_count": r[1],
            "wrong_count": r[2],
            "last_practiced_at": r[3],
        }
        for r in res.all()
    ]
    return plan_review(rows)


async def recommend(
    db: AsyncSession,
    student_id: str,
    n: int = 5,
    subject: Optional[str] = None,
) -> dict:
    """自适应推题：ZPD 内按难度带选题（ weakest-first ）。

    返回 {student_id, subject, n, zpd, items, review}：
      - items: 每题带 target_kp / band / 该点当前掌握度（前端可解释「为什么推这题」）
      - review: 间隔重复计划（练过但正在遗忘的点）
    选课与 T-A4 组卷接口共用同一套 course_kb 查询（T-A4 落地后此处可改为委托）。
    """
    n = max(1, min(int(n or 5), 20))
    z = await compute_zpd_db(db, student_id, subject)
    zpd: List[str] = z["zpd"]
    mmap: Dict[str, Optional[float]] = z["mastery"]
    review = await review_plan_db(db, student_id, subject)
    if not zpd:
        return {
            "student_id": student_id, "subject": subject, "n": n,
            "zpd": [], "items": [], "review": review,
            "note": "ZPD 为空：所有知识点均已掌握（或前置未就绪被阻塞）",
        }

    items: List[dict] = []
    seen: Set[str] = set()
    for kp in zpd:
        if len(items) >= n:
            break
        band = difficulty_band(mmap.get(kp))
        lo, hi = band_to_difficulty(band)
        # 选课查询与 T-A4 组卷/变式共用同一套 course_kb 读取口径（compose.fetch_questions）
        pool = await compose.fetch_questions(
            db, [kp], difficulty_lo=lo, difficulty_hi=hi,
            subject=subject, limit=8,
        )
        for r in pool:
            h = r.get("content_hash")
            if not h or h in seen:
                continue
            seen.add(h)
            items.append({
                "target_kp": kp,
                "band": band,
                "mastery": mmap.get(kp),
                "id": r["id"],
                "subject": r["subject"],
                "type": r["type"],
                "stem": r["stem"],
                "options": r["options"],
                "answer": r["answer"],
                "analysis": r["analysis"],
                "difficulty": r["difficulty"],
                "knowledge_points": r["knowledge_points"],
                "content_hash": h,
            })
            break                                     # 每个 ZPD 点先出一题，尽量覆盖多点
    return {
        "student_id": student_id, "subject": subject, "n": n,
        "zpd": zpd, "items": items, "review": review,
    }
