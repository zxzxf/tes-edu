"""M4-2 规则组卷引擎（纯函数层，不碰 DB）。

卡面五条硬要求逐条对应：

1. **候选过滤**由服务薄层做（published / 非 quarantine / 有答案 / 卷型题型白名单）；
   本模块只处理已过滤的候选集。
2. **同卷 content_hash 不重复** —— :func:`pick_questions` 里 ``seen`` 去重。
3. **权重**：知识点覆盖优先（每个目标点先保底 1 题）→ 其次难度配比 →
   同类题优先 ``teacher > import > crawl > reviewed > llm_generated``。
4. **题源不足显式报错** —— :class:`ComposeError` 带上缺哪个知识点/难度档。
5. **难度比例可配置**，抽不满时 :func:`summarize_distribution` 回报实际分布与缺口。

为什么单独抽一层纯函数：组卷是「会算错但不该报错」的典型场景——静默凑卷
（少一个知识点、难度全挤在一档）在教师端看不出来，学生端却会得到一张
覆盖不全的卷子。把它做成可单测的纯函数，才能在 CI 里钉住。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

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
    - 无 ``required_kps``：按全池题量均分（用于"随便出一套"）。
    """
    count = max(0, int(count or 0))
    req = [str(k) for k in (required_kps or []) if k]

    by_kp: Dict[str, List[Any]] = {}
    for q in pool or []:
        for kp in _kps(q):
            by_kp.setdefault(kp, []).append(q)

    if not req:
        if not by_kp:
            # 整池都没有知识点标签（存量导入题常见）：按「不限知识点」整池配额。
            # 否则 quota 为空 -> 一道题都抽不出来，教师只看到「可用题 0 道」。
            return {UNSPECIFIED_KP: min(count, len(pool or []))}
        counts = {kp: len(rows) for kp, rows in by_kp.items()}
        return allocate_by_kp(counts, count)

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
    """按配额抽题。

    - 每个知识点内部：先按 ``difficulty_mix`` 尽量满足难度配比，再按
      :func:`_sort_for_pick` 的偏好补足；
    - **同卷 content_hash 不重复**（跨知识点共享去重）；
    - 某个知识点池子不够时抛 :class:`ComposeError`（含缺口维度），
      由调用方决定是降题量还是报 409——绝不静默少抽。
    """
    quota = {k: int(v or 0) for k, v in (quota or {}).items() if v}
    if not quota:
        return []

    by_kp: Dict[str, List[Any]] = {}
    for q in pool or []:
        for kp in _kps(q):
            by_kp.setdefault(kp, []).append(q)

    used_hashes: set = set()
    picked: List[Any] = []
    short: List[str] = []

    def _take(candidates: Iterable[Any], want: int) -> int:
        taken = 0
        for q in _sort_for_pick(list(candidates)):
            if taken >= want:
                break
            h = str(_get(q, "content_hash", "") or "")
            key = h or f"__obj_{id(q)}"
            if key in used_hashes:
                continue
            used_hashes.add(key)
            picked.append(q)
            taken += 1
        return taken

    for kp, want in quota.items():
        rows = list(pool or []) if kp == UNSPECIFIED_KP else (by_kp.get(kp) or [])
        label = "题库" if kp == UNSPECIFIED_KP else f"知识点 {kp}"
        if not rows:
            short.append(f"{label}无题（需 {want} 道）")
            continue
        got = 0
        if difficulty_mix:
            for diff, n in sorted(difficulty_mix.items()):
                target = min(int(n or 0), want - got)
                if target <= 0:
                    continue
                subset = [q for q in rows if str(_get(q, "difficulty", "")) == str(diff)]
                got += _take(subset, target)
        if got < want:
            got += _take(rows, want - got)
        if got < want:
            short.append(f"{label}题源不足（需 {want}，实际 {got}）")

    if short:
        raise ComposeError("；".join(short), shortfall=short)
    return picked