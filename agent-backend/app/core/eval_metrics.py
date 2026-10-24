"""S6 评测脚手架的纯函数层（T4）：金标准解析 + 指标计算 + κ + 报告渲染。

放 app/core 下（而非 scripts/）是为了可被单测直接 import——脚本
scripts/eval_attribution.py 只做 IO 编排（读 CSV、调 judge、归档）。

三张评测表（口径与《归因线收尾与验收任务计划》T4 一致）：
  1. 客观题查表命中率（attribution_source=map 占比，应 ~100%，零 token）
  2. 解答题首错步骤对齐准确率（要求 ≥85%）
  3. 错因分类准确率 + unknown 率（unknown 要求 ≤10%）
  附：评分人 A/B 的 Cohen's κ（标注质量自检）

mock_judge：确定性假判定器，让脚手架不连库、不调 LLM 也能自测跑通
（--mock-noise 可按 hash 稳定扰动一部分行，验证指标数字会变）。
"""
from __future__ import annotations

import csv
import hashlib
from typing import Dict, List, Optional, Sequence, Tuple

# 金标准 CSV 列（含题型列——决定走查表还是 LLM 判分路线，教师按题型走不同标注流程）
GOLD_FIELDS = (
    "content_hash", "题型", "step_no", "学生作答过程",
    "金标准首错步骤", "金标准错因", "评分人A", "评分人B", "仲裁结果",
)
GOLD_REQUIRED = ("content_hash", "学生作答过程")

# judge v2 taxonomy 七类 + unknown（与 app.core.judge_v2 同口径）
TAXONOMY = ("concept", "formula", "case", "calc", "read", "logic", "strategy")
UNKNOWN = "unknown"
OBJECTIVE_TYPES = ("单选", "多选", "填空")
SOLVE_TYPES = ("解答", "实验", "证明")


class GoldFormatError(ValueError):
    """金标准 CSV 缺列/缺必填值——立即报错，不静默跳过。"""


def load_gold_csv(path: str) -> List[dict]:
    """读金标准 CSV（容忍 BOM；缺列或缺必填值直接抛 GoldFormatError）。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in GOLD_FIELDS if c not in header]
        if missing:
            raise GoldFormatError(f"金标准 CSV 缺列: {missing}（需要 {list(GOLD_FIELDS)}）")
        rows: List[dict] = []
        for i, raw in enumerate(reader, start=2):     # 第 1 行是表头
            row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
            for col in GOLD_REQUIRED:
                if not row.get(col):
                    raise GoldFormatError(
                        f"第 {i} 行缺必填值: {col}（content_hash=({row.get('content_hash') or '空'}）"
                    )
            rows.append(row)
    return rows


def to_int(v) -> Optional[int]:
    """宽松整数解析（空/None/非数字 -> None）。"""
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def norm_et(v) -> Optional[str]:
    """错因码归一化：小写去空；空 -> None。"""
    s = (str(v or "").strip().lower())
    return s or None


# --------------------------------------------------------------------------- #
# 指标（纯函数：rows 与 results 逐行对齐）
# --------------------------------------------------------------------------- #

def objective_map_rate(rows: Sequence[dict], results: Sequence[dict]) -> dict:
    """表一：客观题查表命中率（attribution_source == 'map' 占比）。"""
    total = hit = 0
    for row, res in zip(rows, results):
        if (row.get("题型") or "").strip() not in OBJECTIVE_TYPES:
            continue
        if res.get("need_process"):
            continue
        total += 1
        if res.get("attribution_source") == "map":
            hit += 1
    return {
        "total": total, "map_hit": hit,
        "rate": round(hit / total, 4) if total else None,
    }


def first_wrong_step_accuracy(rows: Sequence[dict], results: Sequence[dict]) -> dict:
    """表二：解答题首错步骤对齐准确率。

    全对行：金标准首错步骤为空，judge 返回 None 即算对齐（ None == None ）。
    need_process 行（学生没贴过程）不计入分母，单独报数。
    """
    total = hit = 0
    skipped = 0
    misses: List[Tuple[str, Optional[int], Optional[int]]] = []
    for row, res in zip(rows, results):
        if (row.get("题型") or "").strip() not in SOLVE_TYPES:
            continue
        if res.get("need_process"):
            skipped += 1
            continue
        total += 1
        gold = to_int(row.get("金标准首错步骤"))
        got = to_int(res.get("first_wrong_step"))
        if (gold or None) == (got or None):
            hit += 1
        elif len(misses) < 10:
            misses.append((row.get("content_hash", "")[:12], gold, got))
    return {
        "total": total, "hit": hit,
        "accuracy": round(hit / total, 4) if total else None,
        "skipped_need_process": skipped,
        "misses": misses,
    }


def error_type_metrics(rows: Sequence[dict], results: Sequence[dict]) -> dict:
    """表三：错因分类准确率 + unknown 率。

    - 准确率分母：金标准标了错因的行（全对行双方皆空不计）；
    - unknown 率分母：全部参与判分的行（非 need_process）——unknown 是判分器
      「不硬猜」的出口，占比过高说明标注/题库覆盖不足。
    """
    judged = hit = unknown = 0
    confusion: Dict[str, Dict[str, int]] = {}
    for row, res in zip(rows, results):
        if res.get("need_process"):
            continue
        judged += 1
        gold = norm_et(row.get("金标准错因"))
        got = norm_et(res.get("error_type"))
        if got == UNKNOWN:
            unknown += 1
        if gold is None:
            continue                                    # 全对行不计入分类准确率
        if got == gold:
            hit += 1
        else:
            confusion.setdefault(gold, {}).setdefault(got or "none", 0)
            confusion[gold][got or "none"] += 1
    return {
        "judged": judged,
        "labeled": sum(sum(d.values()) for d in confusion.values()) + hit,
        "hit": hit,
        "accuracy": round(hit / (hit + sum(sum(d.values()) for d in confusion.values())), 4)
        if (hit or confusion) else None,
        "unknown": unknown,
        "unknown_rate": round(unknown / judged, 4) if judged else None,
        "confusion": confusion,
    }


def cohens_kappa(labels_a: Sequence[str], labels_b: Sequence[str]) -> Optional[float]:
    """Cohen's κ：两位评分人标注一致性（po=观察一致率，pe=随机一致率）。

    任一侧空（不足 2 个样本）或 pe>=1（完全同分布，κ 无定义）返回 None。
    """
    a = list(labels_a)
    b = list(labels_b)
    n = len(a)
    if n == 0 or len(b) != n:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)


def rater_kappa(rows: Sequence[dict]) -> dict:
    """评分人 A/B 一致性（只取双方均标注错因的行；留空视为一类'无错'）。"""
    pairs = [
        (norm_et(r.get("评分人A")) or "none", norm_et(r.get("评分人B")) or "none")
        for r in rows
        if (r.get("评分人A") or "").strip() or (r.get("评分人B") or "").strip()
    ]
    k = cohens_kappa([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None
    agree = sum(1 for x, y in pairs if x == y)
    return {
        "n": len(pairs), "agree": agree,
        "po": round(agree / len(pairs), 4) if pairs else None,
        "kappa": k,
    }


# --------------------------------------------------------------------------- #
# mock 判定器（脚手架自测：不连库、不调 LLM）
# --------------------------------------------------------------------------- #

def mock_judge(row: dict, noise: float = 0.0) -> dict:
    """确定性假 judge：按金标准行推导判定结果，noise 按 content_hash 稳定扰动。

    - 客观题：模拟查表命中（attribution_source=map）；
    - 解答题：模拟 LLM 判定（attribution_source=llm）；
    - noise>0 时，hash 尾数落在噪声区间内的行被扰动（首错步骤 +1、错因换类），
      用于验证「指标数字会随判定质量变化」而非恒真。
    """
    h = (row.get("content_hash") or "").strip()
    qtype = (row.get("题型") or "").strip()
    gold_step = to_int(row.get("金标准首错步骤"))
    gold_et = norm_et(row.get("金标准错因"))

    perturbed = noise > 0 and (
        int(hashlib.md5(h.encode("utf-8")).hexdigest()[-6:], 16) % 1000
    ) < noise * 1000
    if perturbed:
        step = (gold_step or 0) + 1
        et = "logic" if gold_et != "logic" else "calc"
    else:
        step, et = gold_step, gold_et

    if qtype in OBJECTIVE_TYPES:
        src = "map"
    else:
        src = "llm"
    return {
        "correct": et is None and step is None,
        "partial": False,
        "comment": "mock 判定（脚手架自测）",
        "first_wrong_step": step,
        "error_type": et,
        "evidence": "",
        "correction": "",
        "attribution_source": src,
        "need_process": False,
    }


# --------------------------------------------------------------------------- #
# 报告渲染（Markdown 三张表 + 结论）
# --------------------------------------------------------------------------- #

def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def render_report(
    batch: str,
    gold_path: str,
    n_rows: int,
    obj: dict,
    step: dict,
    et: dict,
    kappa: dict,
    thresholds: Optional[dict] = None,
) -> str:
    """渲染 Markdown 评测报告（三张表 + 阈值判定 + 结论）。"""
    th = thresholds or {"objective": 0.95, "first_wrong_step": 0.85, "unknown": 0.10}

    def verdict(value: Optional[float], floor: float, ceiling: bool = False) -> str:
        if value is None:
            return "⚪ 无数据"
        ok = value <= floor if ceiling else value >= floor
        return "✅ 达标" if ok else "❌ 未达标"

    lines: List[str] = []
    lines.append(f"# S6 归因评测报告 · {batch}")
    lines.append("")
    lines.append(f"- 金标准：`{gold_path}`（{n_rows} 行）")
    lines.append(f"- 判分器：judge v2（客观题查表零 token / 解答题 LLM 对齐参考步骤）")
    lines.append("")
    lines.append("## 表一 · 客观题查表命中率")
    lines.append("")
    lines.append("| 指标 | 数值 | 判定 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 查表命中（map） | {obj['map_hit']}/{obj['total']} = {_pct(obj['rate'])} "
        f"| {verdict(obj['rate'], th['objective'])}（阈值 ≥95%） |"
    )
    lines.append("")
    lines.append("## 表二 · 解答题首错步骤对齐准确率")
    lines.append("")
    lines.append("| 指标 | 数值 | 判定 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 首错步骤对齐 | {step['hit']}/{step['total']} = {_pct(step['accuracy'])} "
        f"| {verdict(step['accuracy'], th['first_wrong_step'])}（阈值 ≥85%） |"
    )
    if step["skipped_need_process"]:
        lines.append(f"| 未贴过程（不计入） | {step['skipped_need_process']} 行 | — |")
    if step["misses"]:
        lines.append("")
        lines.append("未对齐样例（hash / 金标准 / judge 判定）:")
        for h, g, got in step["misses"][:10]:
            lines.append(f"- `{h}` 金标准={g} judge={got}")
    lines.append("")
    lines.append("## 表三 · 错因分类准确率 + unknown 率")
    lines.append("")
    lines.append("| 指标 | 数值 | 判定 |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 错因分类准确率 | {et['hit']}/{et['labeled']} = {_pct(et['accuracy'])} "
        f"| {verdict(et['accuracy'], 0.80)}（建议 ≥80%） |"
    )
    lines.append(
        f"| unknown 率 | {et['unknown']}/{et['judged']} = {_pct(et['unknown_rate'])} "
        f"| {verdict(et['unknown_rate'], th['unknown'], ceiling=True)}（要求 ≤10%） |"
    )
    if et["confusion"]:
        lines.append("")
        lines.append("混淆矩阵（金标准 → judge 判定: 次数）:")
        for gold, dist in sorted(et["confusion"].items()):
            for got, c in sorted(dist.items()):
                lines.append(f"- {gold} → {got}: {c}")
    lines.append("")
    lines.append("## 附 · 评分人一致性（Cohen's κ）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---|")
    lines.append(f"| 评分人对数 | {kappa['n']} |")
    lines.append(f"| 观察一致率 po | {_pct(kappa['po'])} |")
    lines.append(f"| Cohen's κ | {'—' if kappa['kappa'] is None else kappa['kappa']} |")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    obj_ok = obj["rate"] is not None and obj["rate"] >= th["objective"]
    step_ok = step["accuracy"] is not None and step["accuracy"] >= th["first_wrong_step"]
    et_ok = et["unknown_rate"] is not None and et["unknown_rate"] <= th["unknown"]
    if obj_ok and step_ok and et_ok:
        lines.append("三项硬指标全部达标，归因判定质量可用，S6 可出结论。")
    else:
        bad = [n for n, ok in (("查表命中率", obj_ok), ("首错步骤对齐", step_ok), ("unknown 率", et_ok)) if not ok]
        lines.append(f"未达标项：{'、'.join(bad)}——按混淆矩阵/未对齐样例定位问题后复测。")
    lines.append("")
    return "\n".join(lines)
