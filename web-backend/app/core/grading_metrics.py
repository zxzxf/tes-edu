"""判分指标口径（M2-7）：纯函数，不碰 DB / ORM。

为什么口径要单独成一个模块
--------------------------
面板上的百分比如果散落在 SQL 与模板里各算一遍，早晚会出现「看板说覆盖率 97%、
详情页说 99%」这种没法解释的矛盾。这里把**每个字的定义**钉死，聚合层只负责数数，
前端只负责画。改口径只改这一个文件，测试也只测这一个文件。

三个核心指标（口径来自《需求变更-任务方案书》§8.3）
---------------------------------------------------
1. **判据覆盖率** = 走确定性判定的题数 / 可自动阅卷题数（choice + blank）。
   「走确定性判定」= ``exam_submission_items.status == 'auto'``：机器自己给了结论。
   分母**只含 choice + blank**——解答题本来就该人判，算进去会永远不达标。
   目标：稳定期 ≥99%，首月 ≥90%。
2. **人工复核率** = 需人工判定的题数（manual_pending + manual_reviewed）/ 总题数。
   方案书原文写的是 ``manual_pending / 总题数``，但题一旦被判完就从 pending 变成
   reviewed，用瞬时 pending 数会得出「越勤快批改、复核率越低」的荒谬结论。所以
   这里用**曾经需要人判**的口径（两个状态之和），并另给 ``backlog`` 表示当前积压。
   目标：客观填空部分 ≤3%（首月 ≤10%）。
3. **判分准确率** = 1 − 教师改判题数 / 已自动判题数。
   分母是**机器判过的题**。本系统当前只有「机器判不了 → 老师判」这一条入口，
   机器判过的题没有任何复核入口，所以这个指标**现在不可测**——``accuracy`` 会
   显式返回 ``measurable=False`` 并说明原因，而不是伪造一个 100%。
   （等 M4-7 抽样复核上线，同一段代码不用改就能出数。）

关于「分母为 0」
----------------
0/0 不是 0%。``ratio`` 在分母为 0 时返回 ``None``，前端显示「暂无数据」。
把无数据渲染成 0% 会让老师以为「判据全挂了」，比不显示更糟。

关于判据来源（``grading_source``）
----------------------------------
方案书里这个位置叫 ``attribution_source``（现代码里指错因归因 map/llm/unknown）。
判分链路上它回答的是同一个问题：**这一题的结论是哪条判据给的**。每题一条，
落在 ``exam_submission_items.grading_source``，指标面板据此出分布——
「覆盖率掉了」到底是解析能力退化（manual_symbol 变多）还是标答被冻结
（manual_frozen），看分布一眼就分清。
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

# ---------------------------------------------------------------- 判据来源编码
# 约定：auto_* 是机器给出结论的判据，manual_* 是转人工的原因。
# 前缀本身就是语义，``is_manual_source`` 直接据此判断，不维护第二份清单。
SOURCE_CHOICE = "choice_map"              # 选择题：选项字母表等价（含多选集合）
SOURCE_BLANK_SAME = "blank_same"          # 填空题：归一后与标答完全一致（红线短路）
SOURCE_BLANK_VALUE = "blank_value"        # 填空题：命中教师确认过的可接受答案值
SOURCE_BLANK_EQUIV = "blank_equiv"        # 填空题：数值 / 区间等价判定
SOURCE_BLANK_EMPTY = "blank_empty"        # 填空题：未作答（明确判错，不算判不了）
SOURCE_MANUAL_FROZEN = "manual_frozen"    # 转人工：该题标答已被判分护栏冻结
SOURCE_MANUAL_SYMBOL = "manual_symbol"    # 转人工：答案含字母/符号，无法证明等价
SOURCE_MANUAL_BLANKS = "manual_blanks"    # 转人工：多空题空数不符
SOURCE_MANUAL_NO_ANSWER = "manual_no_answer"      # 转人工：标答为空
SOURCE_MANUAL_SUBJECTIVE = "manual_subjective"    # 转人工：解答题
SOURCE_UNKNOWN = "unknown"                # 历史数据（加列之前落的行）

SOURCE_LABELS: Dict[str, str] = {
    SOURCE_CHOICE: "选择题选项比对",
    SOURCE_BLANK_SAME: "填空·与标答一致",
    SOURCE_BLANK_VALUE: "填空·命中答案值",
    SOURCE_BLANK_EQUIV: "填空·等价判定",
    SOURCE_BLANK_EMPTY: "填空·未作答",
    SOURCE_MANUAL_FROZEN: "转人工·标答冻结",
    SOURCE_MANUAL_SYMBOL: "转人工·符号表达式",
    SOURCE_MANUAL_BLANKS: "转人工·空数不符",
    SOURCE_MANUAL_NO_ANSWER: "转人工·标答为空",
    SOURCE_MANUAL_SUBJECTIVE: "转人工·解答题",
    SOURCE_UNKNOWN: "未记录（历史数据）",
}


def source_label(code: Optional[str]) -> str:
    return SOURCE_LABELS.get(code or SOURCE_UNKNOWN, code or SOURCE_UNKNOWN)


def is_manual_source(code: Optional[str]) -> bool:
    """该来源是否表示「转人工」。未知来源不算人工（历史数据不猜）。"""
    return bool(code) and code.startswith("manual_")


# ---------------------------------------------------------------- 基础算术
def ratio(numerator: int, denominator: int) -> Optional[float]:
    """0~1 的比率；**分母为 0 返回 None**（无数据 ≠ 0%）。"""
    try:
        d = int(denominator)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(float(numerator) / d, 6)


def grade_status(
    value: Optional[float], stable: float, watch: float
) -> str:
    """达标状态：``ok`` 达标 / ``watch`` 首月过渡可接受 / ``below`` 不达标 / ``unknown``。

    两档阈值来自方案书（稳定期 / 首月过渡）。分两档而不是一个，是因为上线首月
    必然达不到稳定期标准；只给一个红叉会让团队对整块面板脱敏。
    """
    if value is None:
        return "unknown"
    if value >= stable:
        return "ok"
    if value >= watch:
        return "watch"
    return "below"


def grade_status_lower(value: Optional[float], stable: float, watch: float) -> str:
    """**越低越好**的指标（人工复核率）达标状态。

    别用 ``grade_status`` 硬套：那个函数假设「大 = 好」，套到复核率上会把 3%
    判成不达标、把 90% 判成达标，两个方向全反。
    """
    if value is None:
        return "unknown"
    if value <= stable:
        return "ok"
    if value <= watch:
        return "watch"
    return "below"


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """线性插值分位数（与 numpy.percentile 默认口径一致）。

    用线性插值而不是「取第 k 个」：时效样本通常只有个位数，最近秩法会让 P90
    永远等于最大值，看着像「最慢那一单」而不是分位数。
    """
    xs = sorted(float(v) for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 1)
    k = (len(xs) - 1) * (float(p) / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return round(xs[int(k)], 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


# ---------------------------------------------------------------- 指标组装
# 阈值常量（方案书 §8.3 目标），集中一处便于按学期调整
COVERAGE_STABLE = 0.99
COVERAGE_FIRST_MONTH = 0.90
MANUAL_RATE_STABLE = 0.03
MANUAL_RATE_FIRST_MONTH = 0.10


def coverage_metric(auto_count: int, gradable_count: int) -> dict:
    """判据覆盖率。``gradable`` = choice + blank 的题量。"""
    v = ratio(auto_count, gradable_count)
    return {
        "value": v,
        "numerator": int(auto_count),
        "denominator": int(gradable_count),
        "target": COVERAGE_STABLE,
        "status": grade_status(v, COVERAGE_STABLE, COVERAGE_FIRST_MONTH),
        "note": "分母只含选择题与填空题（解答题本就该人判，算进去永不达标）",
    }


def manual_rate_metric(manual_count: int, total_count: int) -> dict:
    """人工复核率（曾经需要人判的题 / 总题数）。"""
    v = ratio(manual_count, total_count)
    return {
        "value": v,
        "numerator": int(manual_count),
        "denominator": int(total_count),
        "target": MANUAL_RATE_STABLE,
        "status": grade_status_lower(v, MANUAL_RATE_STABLE, MANUAL_RATE_FIRST_MONTH),
        "note": "口径为「曾经需要人判」（待人工 + 已复核）；当前积压见 backlog",
    }


def accuracy_metric(
    auto_count: int, reviewed_auto_count: int, overturned_count: int
) -> dict:
    """判分准确率 = 1 − 改判数 / 已自动判题数。

    ``reviewed_auto_count`` 是**被复核过的机器判分题数**。它为 0 时指标不可测：
    没有被复核的样本，任何百分比都是编的。这里如实返回 ``measurable=False``。
    """
    if reviewed_auto_count <= 0:
        return {
            "value": None,
            "numerator": 0,
            "denominator": int(auto_count),
            "target": 0.98,
            "status": "unknown",
            "measurable": False,
            "note": (
                "暂无对机器判分题的复核样本：当前只有「机器判不了→老师判」这一条"
                "入口，机器已判的题没有复核入口。抽样复核（M4-7）上线后自动出数。"
            ),
        }
    v = ratio(max(reviewed_auto_count - overturned_count, 0), reviewed_auto_count)
    return {
        "value": v,
        "numerator": int(reviewed_auto_count - overturned_count),
        "denominator": int(reviewed_auto_count),
        "target": 0.98,
        "status": grade_status(v, 0.98, 0.95),
        "measurable": True,
        "note": f"已复核 {reviewed_auto_count} 题，其中改判 {overturned_count} 题",
    }


def source_rows(counts: Dict[str, int], total: Optional[int] = None) -> List[dict]:
    """判据来源分布：按数量降序，附占比与中文标签。

    ``unknown`` 永远排最后——它只是「加列之前的历史行」，不是一种判据，
    混在中间会让人以为系统有第四种判分方式。
    """
    base = int(total if total is not None else sum(counts.values()))
    rows = [
        {
            "source": code,
            "label": source_label(code),
            "count": int(n),
            "ratio": ratio(n, base),
            "manual": is_manual_source(code),
        }
        for code, n in counts.items()
        if n
    ]
    rows.sort(key=lambda r: (r["source"] == SOURCE_UNKNOWN, -r["count"]))
    return rows


def sum_counts(row_dicts: Iterable[dict], key: str = "source") -> Dict[str, int]:
    """把分组查询结果累加成 ``{key: count}``（SQL 已分组时通常不需要）。"""
    out: Dict[str, int] = {}
    for r in row_dicts:
        k = r.get(key)
        if k:
            out[k] = out.get(k, 0) + int(r.get("count") or 0)
    return out
