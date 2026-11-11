"""M4-2 规则组卷引擎（纯函数层，不碰 DB）。

卡面五条硬要求逐条对应：

1. **候选过滤**由服务薄层做（published / 非 quarantine / 有答案 / 卷型题型白名单）；
   本模块只处理已过滤的候选集。
2. **同卷 content_hash 不重复** —— :func:`pick_questions` 里 ``used_hashes`` 去重。
3. **权重**：题目级贪心最大覆盖（每轮选「新增覆盖知识点最多」的题，一题多标签
   天然规避共享题去重冲突）→ 其次难度配比 →
   同类题优先 ``teacher > import > crawl > reviewed > llm_generated``。
4. **题源不足显式报错** —— :class:`ComposeError` 带上缺哪个知识点/难度档。
5. **难度比例可配置**，抽不满时 :func:`summarize_distribution` 回报实际分布与缺口。

为什么单独抽一层纯函数：组卷是「会算错但不该报错」的典型场景——静默凑卷
（少一个知识点、难度全挤在一档）在教师端看不出来，学生端却会得到一张
覆盖不全的卷子。把它做成可单测的纯函数，才能在 CI 里钉住。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

#: 题源优先级：数值越小越优先（卡面 v1.4 定案 teacher > import > crawl > llm_generated）
SOURCE_PRIORITY: Dict[str, int] = {
    "teacher": 0,
    "import": 1,
    "crawl": 2,
    "auto_crawl": 2,
    "reviewed": 2,
    "llm_generated": 3,
}
DEFAULT_SOURCE_PRIORITY = 2

#: 「不限知识点」的配额键：整池题都没有知识点标签时使用（存量导入题常见）。
UNSPECIFIED_KP = ""


class ComposeError(ValueError):
    """组卷失败（题源不足 / 目标知识点无题）。

    调用方据此返回 409 并把 ``detail`` 原样给教师看——**不静默凑卷**。
    """

    def __init__(self, message: str, *, missing_kps: Optional[Sequence[str]] = None,
                 shortfall: Optional[Sequence[str]] = None):
        super().__init__(message)
        self.message = message
        self.missing_kps = list(missing_kps or [])
        self.shortfall = list(shortfall or [])


def _get(q: Any, key: str, default=None):
    """兼容 ORM 对象与 dict。"""
    if isinstance(q, Mapping):
        return q.get(key, default)
    return getattr(q, key, default)


def _kps(q: Any) -> List[str]:
    return [str(k) for k in (_get(q, "knowledge_points", None) or []) if k]


def _source_rank(q: Any) -> int:
    tag = (_get(q, "source_tag", "") or "").strip().lower()
    return SOURCE_PRIORITY.get(tag, DEFAULT_SOURCE_PRIORITY)


def _sort_for_pick(pool: Sequence[Any]) -> List[Any]:
    """稳定排序：教师题优先，其次难度适中（3 先于 1/5 的极端），最后按 hash 稳定。"""
    def key(q):
        d = _get(q, "difficulty", None)
        try:
            dist = abs(int(d) - 3) if d is not None else 2
        except (TypeError, ValueError):
            dist = 2
        return (_source_rank(q), dist, str(_get(q, "content_hash", "") or ""))

    return sorted(pool, key=key)


def allocate_by_kp(counts: Mapping[str, int], count: int) -> Dict[str, int]:
    """按各知识点可选题量**均分**配额，总量收敛到 min(count, 可选总数)。

    均分用「余量轮」补齐：先按可用量升序各给 1，再轮流加，直到用完或受限于可用量。
    """
    count = max(0, int(count or 0))
    avail = {k: max(0, int(v or 0)) for k, v in (counts or {}).items() if v}
    if count <= 0 or not avail:
        return {}

    quota = {k: 0 for k in avail}
    remaining = min(count, sum(avail.values()))

    # 第一轮：按可用量升序（少的多给，避免小点被饿死）
    for k in sorted(avail, key=lambda x: (avail[x], x)):
        if remaining <= 0:
            break
        quota[k] += 1
        remaining -= 1

    # 余量轮：按可用量降序轮流加
    order = sorted(avail, key=lambda x: (-avail[x], x))
    while remaining > 0:
        progressed = False
        for k in order:
            if remaining <= 0:
                break
            if quota[k] < avail[k]:
                quota[k] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return quota


def build_quota(
    pool: Sequence[Any],
    required_kps: Sequence[str],
    count: int,
) -> Dict[str, int]:
    """按「知识点覆盖优先」计算配额。

    - 有 ``required_kps``：每个目标点**至少 1 题**；无题的点直接报错点名，
      不静默少一个点。
    - 无 ``required_kps``：返回「整池选 count 道」标记（``UNSPECIFIED_KP``），
      具体覆盖哪些知识点交给 :func:`pick_questions` 的题目级贪心最大覆盖决定。
    """
    count = max(0, int(count or 0))
    req = [str(k) for k in (required_kps or []) if k]

    by_kp: Dict[str, List[Any]] = {}
    for q in pool or []:
        for kp in _kps(q):
            by_kp.setdefault(kp, []).append(q)

    if not req:
        # 不限知识点：不再把配额摊到每个 kp。
        # 真实题库「一题多标签」是常态（2026-11-11 生产验收：69 道选择题 100%
        # 带 2~4 个 kp，131 个 kp 中 63 个只有 1 道题）。按 kp 分配配额会让
        # sum(各 kp 可选题量) 远大于实际题数，且冷门单点 kp 抢占配额后与
        # pick_questions 的全局 content_hash 去重冲突——题量一大必然误报
        # 「题源不足」。改为整池选 count 道，覆盖目标由贪心动态决定。
        return {UNSPECIFIED_KP: count} if count > 0 else {}

    missing = [kp for kp in req if not by_kp.get(kp)]
    if missing:
        raise ComposeError(
            f"题源不足：目标知识点 {'、'.join(missing)} 在过滤后的题库中没有题目"
            f"（请补题、放宽知识点或改用其他卷型）",
            missing_kps=missing,
        )

    # 保底 1 题/点；题量不足以覆盖所有点时点名报错
    if count < len(req):
        raise ComposeError(
            f"题量不足：需覆盖 {len(req)} 个知识点，但只要求 {count} 题",
            missing_kps=[],
        )

    quota = {kp: 1 for kp in req}
    remaining = count - len(req)
    if remaining > 0:
        extra_counts = {kp: len(by_kp[kp]) - 1 for kp in req}
        extra = allocate_by_kp(extra_counts, remaining)
        for kp, n in extra.items():
            quota[kp] += n
    return quota


def summarize_distribution(
    picked: Sequence[Any],
    difficulty_mix: Optional[Mapping[int, int]] = None,
) -> Dict[str, Any]:
    """实际分布 + 缺口说明（卡面第 5 条：抽不满要回报实际分布）。

    ``difficulty_mix`` 传入期望配比时，逐档比对并给出缺口描述；
    不传则只回报实际分布（老调用方兼容）。
    """
    by_diff: Dict[int, int] = {}
    by_kp: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for q in picked or []:
        d = _get(q, "difficulty", None)
        try:
            di = int(d) if d is not None else 0
        except (TypeError, ValueError):
            di = 0
        by_diff[di] = by_diff.get(di, 0) + 1
        for kp in _kps(q):
            by_kp[kp] = by_kp.get(kp, 0) + 1
        tag = (_get(q, "source_tag", "") or "unknown").strip().lower() or "unknown"
        by_source[tag] = by_source.get(tag, 0) + 1
    shortfall: List[str] = []
    if difficulty_mix:
        for diff, want in sorted(difficulty_mix.items()):
            try:
                want_i = int(want or 0)
            except (TypeError, ValueError):
                continue
            got = by_diff.get(int(diff), 0)
            if got < want_i:
                shortfall.append(
                    f"难度 {diff} 题源不足（需 {want_i}，实际 {got}）"
                )
    return {
        "total": len(picked or []),
        "by_difficulty": dict(sorted(by_diff.items())),
        "by_kp": by_kp,
        "by_source": by_source,
        "shortfall": shortfall,
    }


def pick_questions(
    pool: Sequence[Any],
    quota: Mapping[str, int],
    *,
    difficulty_mix: Optional[Mapping[int, int]] = None,
) -> List[Any]:
    """按「题目级贪心最大覆盖」抽题。

    为什么不再「按 kp 配额逐个抽」
    ------------------------------
    真实题库一题常带多个知识点。旧实现先由 :func:`build_quota` 给每个 kp
    分题数配额，再逐个 kp 从 ``by_kp[kp]`` 抽题、用全局 ``content_hash``
    去重——同一道题被计入多个 kp 池，前面的 kp 抽走共享题后，后面的 kp
    必然抽不满而抛「题源不足」。生产上「高三·数学·纯选择·10 题」这种最
    普通的基线场景都出不了卷（2026-11-11 M4-2 验收实测）。

    新语义
    ------
    ``quota`` 解释为：**总题数 = sum(quota.values())** + **需优先覆盖的知识点
    集合 = quota 的具名键**（``UNSPECIFIED_KP`` 表示不限，覆盖目标扩展为整池
    出现过的一切 kp）。不再强求每个 kp 恰好抽到 ``quota[k]`` 道。

    每轮从「尚未选中的题」里挑得分最高的一题：
      1. 主价值 **新增覆盖数**：能填补多少个「还没覆盖够」的目标 kp；
      2. 次价值 **难度缺口**：优先补当前未满足的难度档（传了 mix 时）；
      3. tie-break：题源优先级 → 难度适中 → content_hash
         （沿用 :func:`_sort_for_pick` 的同口径偏好）。

    天然保证：
      - 同卷 ``content_hash`` 不重复（每轮选题对象并跳过已用 hash）；
      - 指定 kp 先各保底覆盖，再用整池补足到总题数；
      - **只在整池去重后题目真的不够、或目标 kp 完全无题时**才抛
        :class:`ComposeError`，不再因多标签共享去重误报。
    """
    quota = {k: int(v or 0) for k, v in (quota or {}).items() if v}
    if not quota:
        return []

    pool = list(pool or [])
    total_want = sum(quota.values())

    # 覆盖目标：具名 kp 用其配额值作软目标（至少 1）；不限 kp 则覆盖整池所有 kp。
    explicit = [k for k in quota if k != UNSPECIFIED_KP]
    if explicit:
        kp_need: Dict[str, int] = {k: max(1, int(quota[k])) for k in explicit}
    else:
        kp_need = {}
        for q in pool:
            for kp in _kps(q):
                kp_need[kp] = 1

    # 目标 kp 完全无题 -> 点名报错（不静默少点）
    present = {kp for q in pool for kp in _kps(q)}
    missing = [k for k in kp_need if k not in present]
    if missing:
        detail = "；".join(f"知识点 {k} 无题" for k in missing)
        raise ComposeError(f"题源不足：{detail}", missing_kps=missing)

    # 难度配比：统计各档期望，抽题时优先补还欠的档（软约束，不强制凑满）
    diff_want: Dict[int, int] = {}
    for d, n in (difficulty_mix or {}).items():
        try:
            di = int(d)
            ni = int(n or 0)
        except (TypeError, ValueError):
            continue
        if ni > 0:
            diff_want[di] = diff_want.get(di, 0) + ni
    diff_got: Dict[int, int] = {d: 0 for d in diff_want}

    def _key(q: Any) -> str:
        h = str(_get(q, "content_hash", "") or "")
        return h or f"__obj_{id(q)}"

    def _diff(q: Any) -> int:
        d = _get(q, "difficulty", None)
        try:
            return int(d) if d is not None else 0
        except (TypeError, ValueError):
            return 0

    def _rank(q: Any):
        """与 _sort_for_pick 同口径的稳定偏好键。"""
        d = _get(q, "difficulty", None)
        try:
            dist = abs(int(d) - 3) if d is not None else 2
        except (TypeError, ValueError):
            dist = 2
        return (_source_rank(q), dist, str(_get(q, "content_hash", "") or ""))

    # 预计算每题元信息，循环里只做增量评分
    entries = [
        {
            "q": q,
            "key": _key(q),
            "kps": set(_kps(q)),
            "diff": _diff(q),
            "rank": _rank(q),
        }
        for q in pool
    ]

    used: set = set()
    picked: List[Any] = []
    covered: Dict[str, int] = {k: 0 for k in kp_need}

    while len(picked) < total_want:
        avail = [e for e in entries if e["key"] not in used]
        if not avail:
            break

        def score(e):
            gain = sum(
                1 for kp in e["kps"]
                if kp in kp_need and covered[kp] < kp_need[kp]
            )
            d_gain = 1 if diff_want.get(e["diff"], 0) > diff_got.get(e["diff"], 0) else 0
            return (-gain, -d_gain, e["rank"])

        best = min(avail, key=score)
        used.add(best["key"])
        picked.append(best["q"])
        for kp in best["kps"]:
            if kp in covered:
                covered[kp] += 1
        if best["diff"] in diff_got:
            diff_got[best["diff"]] += 1

    if len(picked) < total_want:
        label = "、".join(explicit) if explicit else "题库"
        msg = (
            f"题源不足：{label} 可用题仅 {len(picked)} 道（需 {total_want} 道）。"
            "请降低题量、放宽知识点/难度筛选或改用其他卷型"
        )
        raise ComposeError(msg, shortfall=[f"总量 {len(picked)}/{total_want}"])
    return picked
