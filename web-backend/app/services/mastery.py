"""掌握度更新算法（T-B2）：学生做题后更新知识点掌握度向量。

衔接 S3 judge v2 的步骤级判分（first_wrong_step / error_type），支持解答题按步骤
拆分到多个知识点，并沿 kb_kp_prerequisites 的 DAG 做：
  - 答对 -> 该点 + 祖先（前置）节点递归上溢正向信号
  - 答错 -> 该点负向信号 + 沿 DAG 回溯前置薄弱点（诊断；已掌握的祖先不被污染）
  - 遗忘衰减 -> 长期未练按艾宾浩斯曲线衰减（读取时计算 / 定时任务可选）

设计原则（与 web-backend 测试哲学一致）：
  - 所有「算什么、往哪传播」的逻辑是纯函数（不连库、不 IO），便于无 DB 单测；
  - DB 读写集中在带 db 参数的函数里，薄薄一层。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentMastery

# 阈值（与 reports.py 对齐）
WEAK_THRESHOLD = 0.6
MASTERED_THRESHOLD = 0.8
# 艾宾浩斯半衰期（天）：约 30 天没练，有效掌握度衰减到一半
DEFAULT_HALF_LIFE_DAYS = 30.0

# 单步结果（纯数据，便于从事件 / 数据库多源构造）
StepResultLike = Dict | object


@dataclass
class StepResult:
    step_no: int
    is_correct: bool
    kp: Optional[str] = None          # 知识点 code；缺失时由调用方从 course_kb_steps 解析
    error_type: Optional[str] = None


@dataclass
class MasteryUpdatePlan:
    updates: Dict[str, Dict[str, int]]   # kp_code -> {"correct": n, "wrong": n}
    weak_prereqs: Set[str]               # 诊断：回溯到的薄弱前置点
    affected_kps: Set[str]               # 实际被更新的知识点（含祖先上溢）


# --------------------------------------------------------------------------- #
# 纯函数层（不连库，便于测试）
# --------------------------------------------------------------------------- #

def _to_step_result(s) -> Optional[StepResult]:
    """兼容 dict 与 StepResult 两种输入（事件消费端与测试来源不同）。"""
    if isinstance(s, StepResult):
        return s
    if isinstance(s, dict):
        return StepResult(
            step_no=int(s.get("step_no", 0) or 0),
            is_correct=bool(s.get("is_correct")),
            kp=(s.get("kp") or None),
            error_type=(s.get("error_type") or None),
        )
    return None


def walk_up(edges: Sequence[Tuple[str, str]], node: str) -> Set[str]:
    """返回 node 的全部（传递性）前置知识点集合（沿 kp->prereq 向上，不含 node 自身）。

    edges: list of (kp_code, prereq_code)。
    例：边 (B,A),(C,B) 表示 A 是 B 的前置、B 是 C 的前置；walk_up(C)={B,A}。
    """
    adj: Dict[str, List[str]] = {}
    for kp, pre in edges or []:
        adj.setdefault(kp, []).append(pre)
    seen: Set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def wrong_step_nos_for_event(
    first_wrong_step: Optional[int],
    total_steps: int,
    correct: Optional[bool],
) -> Set[int]:
    """由 judge v2 的事件字段推导「出错步骤号集合」（纯函数，便于单测）。

    - correct=True（整题对）-> 空集
    - correct=False/None 且 first_wrong_step 已知 -> step_no >= first_wrong_step
    - correct=False/None 且 first_wrong_step 未知 -> 全部步骤（整题错）
    """
    if correct is True:
        return set()
    if first_wrong_step:
        try:
            f = int(first_wrong_step)
        except (TypeError, ValueError):
            f = 0
        return {n for n in range(1, (total_steps or 0) + 1) if n >= f}
    return set(range(1, (total_steps or 0) + 1))


def derive_step_results(
    steps: Sequence[dict],
    wrong_step_nos: Sequence[int],
) -> List[StepResult]:
    """由「题目的全部步骤 + 出错步骤号集合」推导每步对错（纯函数）。

    steps: [{step_no, kp_code}]；wrong_step_nos: 出错步骤号集合。
    出错步骤 -> is_correct=False；其余 -> is_correct=True。
    """
    wrong = set(wrong_step_nos or [])
    out: List[StepResult] = []
    for s in steps or []:
        no = int((s or {}).get("step_no", 0) or 0)
        out.append(
            StepResult(
                step_no=no,
                is_correct=no not in wrong,
                kp=((s or {}).get("kp_code") or None),
            )
        )
    return out


def plan_mastery_updates(
    step_results: Sequence[StepResult],
    edges: Sequence[Tuple[str, str]],
    current_mastery: Optional[Dict[str, float]] = None,
    weak_threshold: float = WEAK_THRESHOLD,
) -> MasteryUpdatePlan:
    """核心：由步骤结果 + DAG 算出每个知识点应增减的对错计数 + 薄弱前置诊断。

    规则：
      - 某步答对：该步 kp + 其全部祖先（前置）各 +1 正确（向上溢出）
      - 某步答错：仅该步 kp +1 错误（不污染祖先）
                 并沿前置链回溯，把 current_mastery < weak_threshold 的祖先
                 标记为薄弱（诊断）：薄弱前置点本身再 +1 错误（根因加固），
                 已掌握的祖先保持不动。
    """
    current_mastery = current_mastery or {}
    updates: Dict[str, Dict[str, int]] = {}
    weak: Set[str] = set()

    for s in step_results or []:
        if not isinstance(s, StepResult):
            s = _to_step_result(s)
        if not s or not s.kp:
            continue
        if s.is_correct:
            # 答对：该点 + 全部祖先 上溢 +正确
            for node in [s.kp] + list(walk_up(edges, s.kp)):
                u = updates.setdefault(node, {"correct": 0, "wrong": 0})
                u["correct"] += 1
        else:
            # 答错：仅该点负向
            u = updates.setdefault(s.kp, {"correct": 0, "wrong": 0})
            u["wrong"] += 1
            # 回溯前置薄弱点（诊断 + 根因加固，已掌握者不动）
            for pre in walk_up(edges, s.kp):
                score = current_mastery.get(pre, 1.0)  # 缺省当已掌握，避免误扣
                if score < weak_threshold:
                    pu = updates.setdefault(pre, {"correct": 0, "wrong": 0})
                    pu["wrong"] += 1
                    weak.add(pre)

    return MasteryUpdatePlan(
        updates=updates,
        weak_prereqs=weak,
        affected_kps=set(updates.keys()),
    )


def decay_factor(
    last_practiced_at: Optional[datetime],
    now: datetime,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> float:
    """艾宾浩斯遗忘：R = 0.5 ** (Δt / half_life)。未练习过返回 1.0（无衰减）。"""
    if last_practiced_at is None:
        return 1.0
    try:
        days = (now - last_practiced_at).total_seconds() / 86400.0
    except TypeError:
        return 1.0
    if days <= 0:
        return 1.0
    return 0.5 ** (days / half_life_days)


def compute_mastery_score(
    correct_count: int,
    wrong_count: int,
    last_practiced_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> Optional[float]:
    """读取时衰减后的掌握度（0~1）。无判分记录（judged=0）返回 None。

    衰减模型：raw_ratio（原始对错比）乘以遗忘因子 f（艾宾浩斯曲线）。
    含义——长期未练时 f 趋近 0，掌握度随之向「已遗忘」衰减；
    刚练过（f=1）则与原始比例一致。这样衰减才会真正拉低陈旧掌握度，
    而非在分子分母等比例抵消（旧实现对纯对/纯错题无效）。
    """
    now = now or datetime.now(timezone.utc)
    judged = (correct_count or 0) + (wrong_count or 0)
    if judged == 0:
        return None
    raw = (correct_count or 0) / judged
    f = decay_factor(last_practiced_at, now, half_life_days)
    return round(raw * f, 4)


# --------------------------------------------------------------------------- #
# DB 读取层
# --------------------------------------------------------------------------- #

async def load_dag_edges(db: AsyncSession) -> List[Tuple[str, str]]:
    """读取 kb_kp_prerequisites 全部前置边 -> [(kp_code, prereq_code), ...]。"""
    res = await db.execute(text(
        "SELECT kp_code, prereq_code FROM kb_kp_prerequisites"
    ))
    return [(r[0], r[1]) for r in res.all()]


async def load_step_kps(db: AsyncSession, content_hash: str) -> List[dict]:
    """读取某题的全部步骤（step_no, kp_code），供掌握度逐步骤映射。"""
    if not content_hash:
        return []
    res = await db.execute(
        text(
            "SELECT step_no, kp_code FROM course_kb_steps "
            "WHERE content_hash = :h ORDER BY step_no"
        ).bindparams(h=content_hash)
    )
    return [{"step_no": r[0], "kp_code": r[1]} for r in res.all()]


async def load_kp_title_map(db: AsyncSession) -> Dict[str, str]:
    """KP 名称 -> code 映射（kb_knowledge_points.title 唯一，无空值行）。

    存在原因：student_mastery / student_events 的 knowledge_point 历史上按
    **名称**落库（事件流的 KP 抽取产出名称），而 DAG / ZPD / 图谱全部按
    **code** 键组织。读侧统一把名称翻译成 code，两种键的数据都能对上。
    """
    res = await db.execute(text(
        "SELECT title, code FROM kb_knowledge_points "
        "WHERE title IS NOT NULL AND btrim(title) <> ''"
    ))
    return {r[0]: r[1] for r in res.all() if r[0] and r[1]}


async def load_mastery_map(
    db: AsyncSession,
    student_id: str,
    kp_codes: Sequence[str],
    now: Optional[datetime] = None,
) -> Dict[str, float]:
    """读取学生在若干知识点上的当前（衰减后）掌握度，仅含已判分的点。

    未判分的 KPs 不返回（plan 中按默认 1.0=已掌握处理，避免误扣）。

    键兼容：mastery 行按 code 落库的原样用；按名称落库的（历史数据）
    经 load_kp_title_map 翻译成 code 后再匹配，翻译不到的名称忽略
    （属于旧扁平 KP 体系，DAG 中无对应节点）。
    """
    now = now or datetime.now(timezone.utc)
    kp_codes = [k for k in (kp_codes or []) if k]
    if not kp_codes:
        return {}
    res = await db.execute(
        select(StudentMastery).where(StudentMastery.student_id == student_id)
    )
    rows = res.scalars().all()
    if not rows:
        return {}
    wanted = set(kp_codes)
    keys = {r.knowledge_point for r in rows if r.knowledge_point}
    unresolved = keys - wanted
    title_map = await load_kp_title_map(db) if unresolved else {}
    out: Dict[str, float] = {}
    for r in rows:
        kp = r.knowledge_point
        if kp not in wanted:
            kp = title_map.get(kp, kp)
        if kp not in wanted:
            continue
        score = compute_mastery_score(
            r.correct_count, r.wrong_count, r.last_practiced_at, now
        )
        if score is not None:
            out[kp] = score
    return out


# --------------------------------------------------------------------------- #
# DB 写入层
# --------------------------------------------------------------------------- #

async def apply_mastery_updates(
    db: AsyncSession,
    student_id: str,
    updates: Dict[str, Dict[str, int]],
    tenant_id: Optional[str] = None,
    subject: Optional[str] = None,
) -> None:
    """把 plan.updates 原子 upsert 到 student_mastery（按 学生×知识点）。"""
    if not updates:
        return
    now = datetime.now(timezone.utc)
    for kp, delta in updates.items():
        c = delta.get("correct", 0)
        w = delta.get("wrong", 0)
        if not c and not w:
            continue
        stmt = pg_insert(StudentMastery).values(
            student_id=student_id,
            tenant_id=tenant_id,
            subject=subject,
            knowledge_point=kp,
            practice_count=1,
            correct_count=c,
            wrong_count=w,
            last_practiced_at=now,
        )
        set_ = {
            "practice_count": StudentMastery.practice_count + 1,
            "last_practiced_at": now,
            "tenant_id": tenant_id,
            "subject": subject,
        }
        if c:
            set_["correct_count"] = StudentMastery.correct_count + c
        if w:
            set_["wrong_count"] = StudentMastery.wrong_count + w
        stmt = stmt.on_conflict_do_update(
            index_elements=["student_id", "knowledge_point"], set_=set_
        )
        await db.execute(stmt)
    await db.commit()


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #

async def update_mastery(
    db: AsyncSession,
    student_id: str,
    content_hash: str,
    step_results: Sequence,
    tenant_id: Optional[str] = None,
    subject: Optional[str] = None,
) -> MasteryUpdatePlan:
    """直接按步骤结果更新掌握度（供 T-B3 推题 / 测试直接调用）。

    step_results: Sequence[StepResult] 或兼容 dict 列表 [{step_no, is_correct, kp}]。
    """
    sr = [_to_step_result(s) for s in step_results]
    sr = [s for s in sr if s]
    dag_edges = await load_dag_edges(db)
    need = set()
    for s in sr:
        if s.kp and not s.is_correct:
            need.add(s.kp)
            need.update(walk_up(dag_edges, s.kp))
    current = await load_mastery_map(db, student_id, need) if need else {}
    plan = plan_mastery_updates(sr, dag_edges, current)
    await apply_mastery_updates(db, student_id, plan.updates, tenant_id, subject)
    return plan


async def update_mastery_from_event(
    db: AsyncSession,
    event: dict,
    dag_edges: Optional[List[Tuple[str, str]]] = None,
) -> MasteryUpdatePlan:
    """从一次 S3 学情事件更新掌握度（衔接 student_step_errors 步骤级判分）。

    规则：
      - 未判分（correct=None）-> 不更新掌握度（事件流水只计练习量，由 record_event 负责）
      - 有 course_kb_steps -> 按步骤逐一定对错（解答题多步多知识点）
      - 无步骤结构 -> 退化为题目级知识点（event.knowledge_points）按整题对错
    """
    student_id = event.get("student_id") or ""
    content_hash = event.get("content_hash")
    correct = event.get("correct")

    if correct is None:
        return MasteryUpdatePlan(updates={}, weak_prereqs=set(), affected_kps=set())

    if dag_edges is None:
        dag_edges = await load_dag_edges(db)

    steps: List[StepResult] = []
    if content_hash:
        raw_steps = await load_step_kps(db, content_hash)
        if raw_steps:
            total = len(raw_steps)
            first_wrong = event.get("first_wrong_step")
            wrong_set = wrong_step_nos_for_event(first_wrong, total, correct)
            steps = derive_step_results(raw_steps, wrong_set)

    if not steps:
        # 退化：题目级知识点（无步骤结构时保持旧口径）
        for k in (event.get("knowledge_points") or []):
            steps.append(
                StepResult(
                    step_no=0,
                    is_correct=(correct is True),
                    kp=k,
                    error_type=event.get("error_type"),
                )
            )

    if not steps:
        return MasteryUpdatePlan(updates={}, weak_prereqs=set(), affected_kps=set())

    need = set()
    for s in steps:
        if s.kp and not s.is_correct:
            need.add(s.kp)
            need.update(walk_up(dag_edges, s.kp))
    current = await load_mastery_map(db, student_id, need) if need else {}
    plan = plan_mastery_updates(steps, dag_edges, current)
    await apply_mastery_updates(
        db, student_id, plan.updates, event.get("tenant_id"), event.get("subject")
    )
    return plan
