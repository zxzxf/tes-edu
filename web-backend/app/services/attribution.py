"""步骤级归因聚合（S5）：周报步骤级薄弱点 + 老师端错因分布 + 归因来源指标。

设计原则（与 web-backend 现有测试哲学一致）：
- **全部纯函数**，不连库、不发请求，便于无 DB 依赖的单元测试覆盖。
- 输入行既可以是 ORM 对象也可以是 dict（事件消费端与聚合端来源不同）。
- 错因 taxonomy 固定为英文七类 + unknown；**任何中文/大小写/空白输入都先归一化**，
  避免"模型输出中文""存量数据大小写不一"在聚合时出现两个同名分类。

产出三块：
  1. build_step_weakness()  —— 学生周报的"步骤级薄弱点"节
  2. build_class_distribution() —— 老师端班级错因分布（按错因/按题/按步骤）
  3. attribution_source_ratio() —— /metricsz 匿名质量指标（确定性归因占比）
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, Iterable, List, Optional, Sequence

# taxonomy 七类 + unknown（与 agent-backend judge_v2 保持一致）
TAXONOMY = ("concept", "formula", "case", "calc", "read", "logic", "strategy")
UNKNOWN = "unknown"
ERROR_TYPE_ORDER = TAXONOMY + (UNKNOWN,)

ERROR_TYPE_LABELS: Dict[str, str] = {
    "concept": "概念理解错误",
    "formula": "公式误用",
    "case": "分类讨论遗漏",
    "calc": "计算运算错误",
    "read": "审题漏条件",
    "logic": "逻辑跳步",
    "strategy": "方法策略不当",
    "unknown": "待人工复核",
}

# 中文别名 → 英文七类。聚合层再兜一次底：判分链路已归一化，但存量/手写数据仍可能带中文。
_CN_ALIAS: Dict[str, str] = {
    "概念": "concept", "概念理解": "concept", "概念混淆": "concept", "概念错误": "concept",
    "概念理解错误": "concept", "概念不清": "concept",
    "公式": "formula", "公式误用": "formula", "公式记错": "formula", "公式应用错误": "formula",
    "分类讨论": "case", "分类讨论遗漏": "case", "漏分类": "case", "分类不全": "case",
    "讨论不全": "case", "分类讨论错误": "case",
    "计算": "calc", "计算错误": "calc", "运算错误": "calc", "计算运算错误": "calc",
    "运算失误": "calc", "粗心": "calc",
    "审题": "read", "审题错误": "read", "审题漏条件": "read", "漏条件": "read",
    "读题错误": "read", "理解题意错误": "read",
    "逻辑": "logic", "逻辑跳步": "logic", "逻辑错误": "logic", "推理错误": "logic",
    "跳步": "logic",
    "策略": "strategy", "方法策略": "strategy", "方法不当": "strategy", "策略错误": "strategy",
    "方法选择错误": "strategy",
    "未知": "unknown", "待复核": "unknown", "无法判断": "unknown", "其他": "unknown",
}

# 各类错因的一句话学习建议（周报建议 + 老师端教研会素材）
ERROR_TYPE_ADVICE: Dict[str, str] = {
    "concept": "回到定义本身，用自己的话复述一遍概念再做题。",
    "formula": "把公式的适用条件写在题目旁边，先判条件再代公式。",
    "case": "做题前先问「有没有需要分情况讨论的地方」，把分界点列出来。",
    "calc": "运算放慢一步，关键步骤回代验算。",
    "read": "读题时圈出所有条件与限制，做完再核对是否用全。",
    "logic": "每一步都写清「由什么推出什么」，不省略中间依据。",
    "strategy": "先比较两种方法的适用面，再决定用哪种，不要凭直觉上手。",
    "unknown": "该错因尚未定位，建议找老师当面复核这一步。",
}

# 归因来源（agent-backend 口径：map=查表命中 / llm=模型兜底 / unknown=未归因）
ATTRIBUTION_SOURCES = ("map", "llm", "unknown")
# 迁移脚本注释里出现过 table/manual 两个历史写法，指标里统一归并到 map
_SOURCE_ALIAS = {"table": "map", "manual": "map", "rule": "map"}


# ---------------------------------------------------------------- 基础工具

def _get(row: Any, key: str, default: Any = None) -> Any:
    """行取值：兼容 ORM 对象与 dict。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def normalize_error_type(v: Any) -> str:
    """把任意写法收敛到 taxonomy 英文原值；无法识别返回 unknown。"""
    s = str(v or "").strip().lower()
    if not s:
        return UNKNOWN
    if s in ERROR_TYPE_ORDER:
        return s
    if s in _CN_ALIAS:
        return _CN_ALIAS[s]
    # 去空格后再试（"概念 理解" / "概念理解错误"）
    s2 = s.replace(" ", "").replace("_", "")
    if s2 in _CN_ALIAS:
        return _CN_ALIAS[s2]
    for cn, en in _CN_ALIAS.items():
        if cn in s2:
            return en
    return UNKNOWN


def label_of(v: Any) -> str:
    """错因中文名（用于展示）；未知名统一显示"待人工复核"。"""
    return ERROR_TYPE_LABELS.get(normalize_error_type(v), ERROR_TYPE_LABELS[UNKNOWN])


def advice_of(v: Any) -> str:
    return ERROR_TYPE_ADVICE.get(normalize_error_type(v), ERROR_TYPE_ADVICE[UNKNOWN])


def _normalize_source(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ATTRIBUTION_SOURCES:
        return s
    return _SOURCE_ALIAS.get(s, UNKNOWN)


def _ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _step_key(row: Any) -> tuple:
    """步骤键：(content_hash, step_no)。step_no 缺失按 0（未定位到具体步骤）。"""
    h = _get(row, "content_hash") or ""
    try:
        n = int(_get(row, "step_no") or 0)
    except (TypeError, ValueError):
        n = 0
    return (h, n)


def _step_label(step_texts: Optional[Dict[tuple, dict]], key: tuple, step_no: Any) -> str:
    """步骤展示名：优先用步骤文本，退化到「第N步」，step_no=0 显示"未定位步骤"。"""
    n = key[1]
    meta = (step_texts or {}).get(key) or {}
    text = (meta.get("text") or meta.get("label") or "").strip()
    if text:
        return f"第{n}步 · {text}" if n else f"未定位步骤 · {text}"
    if n:
        label = (meta.get("label") or "").strip()
        return f"第{n}步" + (f"（{label}）" if label else "")
    return "未定位步骤"


# ---------------------------------------------------------------- 聚合 ① 错因分布

def count_by_error_type(rows: Iterable[Any]) -> List[dict]:
    """按错因类型计数（固定按七类顺序输出，含占比）。"""
    counts: Dict[str, int] = {t: 0 for t in ERROR_TYPE_ORDER}
    for r in rows or []:
        counts[normalize_error_type(_get(r, "error_type"))] += 1
    total = sum(counts.values())
    out = [
        {
            "error_type": t,
            "label": ERROR_TYPE_LABELS[t],
            "count": counts[t],
            "ratio": _ratio(counts[t], total),
            "advice": ERROR_TYPE_ADVICE[t],
        }
        for t in ERROR_TYPE_ORDER
        if counts[t] > 0
    ]
    out.sort(key=lambda x: (-x["count"], ERROR_TYPE_ORDER.index(x["error_type"])))
    return out


# ---------------------------------------------------------------- 聚合 ② 断点步骤

def top_break_steps(
    rows: Iterable[Any],
    step_texts: Optional[Dict[tuple, dict]] = None,
    top_n: int = 5,
) -> List[dict]:
    """Top 断点步骤：同一 (题, 步骤) 反复出错的次数排序。

    step_texts: {(content_hash, step_no): {"text":..., "label":..., "kp_name":...}}
    """
    buckets: Dict[tuple, dict] = {}
    for r in rows or []:
        k = _step_key(r)
        if not k[0]:
            continue
        b = buckets.setdefault(
            k, {"content_hash": k[0], "step_no": k[1], "count": 0, "types": {}}
        )
        b["count"] += 1
        t = normalize_error_type(_get(r, "error_type"))
        b["types"][t] = b["types"].get(t, 0) + 1

    out: List[dict] = []
    for k, b in buckets.items():
        # 该步骤的主要错因 = 出现次数最多的类型（并列时按七类顺序取先者）
        main_type = max(
            b["types"].items(), key=lambda kv: (kv[1], -ERROR_TYPE_ORDER.index(kv[0]))
        )[0]
        meta = (step_texts or {}).get(k) or {}
        out.append(
            {
                "content_hash": k[0],
                "step_no": k[1],
                "step_label": _step_label(step_texts, k, k[1]),
                "kp_name": (meta.get("kp_name") or ""),
                "count": b["count"],
                "main_error_type": main_type,
                "main_error_label": ERROR_TYPE_LABELS.get(main_type, ERROR_TYPE_LABELS[UNKNOWN]),
                "error_types": dict(sorted(b["types"].items(), key=lambda kv: -kv[1])),
            }
        )
    out.sort(key=lambda x: (-x["count"], x["content_hash"], x["step_no"]))
    return out[: max(0, top_n)]


def build_step_weakness(
    rows: Iterable[Any],
    step_texts: Optional[Dict[tuple, dict]] = None,
    top_n: int = 5,
) -> dict:
    """周报「步骤级薄弱点」节：本周错因分布 + Top 断点步骤 + 一句话结论。"""
    rows = list(rows or [])
    dist = count_by_error_type(rows)
    top = top_break_steps(rows, step_texts, top_n)

    headline = ""
    if top:
        t0 = top[0]
        headline = (
            f"本周在「{t0['step_label']}」这一步错了 {t0['count']} 次"
            f"（{t0['main_error_label']}）。"
        )
    elif dist:
        headline = f"本周共 {len(rows)} 次错因记录，主要集中在{dist[0]['label']}。"

    return {
        "total_errors": len(rows),
        "error_types": dist,
        "top_steps": top,
        "headline": headline,
        "advice": [advice_of(d["error_type"]) for d in dist[:2]],
    }


# ---------------------------------------------------------------- 聚合 ③ 老师端分布

def build_class_distribution(
    rows: Iterable[Any],
    group_by: str = "error_type",
    step_texts: Optional[Dict[tuple, dict]] = None,
    question_titles: Optional[Dict[str, str]] = None,
    top_n: int = 20,
) -> dict:
    """班级错因分布：按错因 / 按题 / 按步骤 三种口径聚合。

    返回 {"group_by", "total", "rows":[{key,label,count,ratio,...}]}
    """
    rows = list(rows or [])
    group_by = group_by if group_by in ("error_type", "question", "step") else "error_type"

    if group_by == "error_type":
        agg = count_by_error_type(rows)
        out = [
            {
                "key": d["error_type"],
                "label": d["label"],
                "count": d["count"],
                "ratio": d["ratio"],
                "advice": d["advice"],
            }
            for d in agg
        ]
        return {"group_by": group_by, "total": len(rows), "rows": out[:top_n]}

    buckets: Dict[Any, dict] = {}
    for r in rows or []:
        k = _step_key(r)
        if group_by == "question":
            key = k[0]
            if not key:
                continue
            title = (question_titles or {}).get(key) or ""
            label = (title[:40] + "…") if len(title) > 40 else (title or key[:12])
        else:
            # key 用完整 hash：老师端要能拿它回查题目/步骤，不能只留截断前缀
            key = f"{k[0]}#{k[1]}" if k[0] else ""
            if not key:
                continue
            label = _step_label(step_texts, k, k[1])
            title = (question_titles or {}).get(k[0]) or ""
            if title:
                label = f"{title[:20]}{'…' if len(title) > 20 else ''} · {label}"

        b = buckets.setdefault(
            key,
            {"key": key, "label": label, "count": 0, "types": {}, "students": set()},
        )
        b["count"] += 1
        t = normalize_error_type(_get(r, "error_type"))
        b["types"][t] = b["types"].get(t, 0) + 1
        sid = _get(r, "student_id")
        if sid:
            b["students"].add(sid)

    out = []
    for b in buckets.values():
        main_type = max(
            b["types"].items(), key=lambda kv: (kv[1], -ERROR_TYPE_ORDER.index(kv[0]))
        )[0]
        row = {
            "key": b["key"],
            "label": b["label"],
            "count": b["count"],
            "ratio": _ratio(b["count"], len(rows)),
            "main_error_type": main_type,
            "main_error_label": ERROR_TYPE_LABELS.get(main_type, ERROR_TYPE_LABELS[UNKNOWN]),
            "student_count": len(b["students"]),
            "error_types": dict(sorted(b["types"].items(), key=lambda kv: -kv[1])),
        }
        if group_by == "step":
            h, _, sn = b["key"].rpartition("#")
            row["content_hash"] = h
            row["step_no"] = int(sn) if sn.isdigit() else 0
            row["step_label"] = b["label"]  # 与 top_break_steps 字段对齐，前端可复用
        else:
            row["content_hash"] = b["key"]
            row["title"] = b["label"]
        out.append(row)
    out.sort(key=lambda x: (-x["count"], x["key"]))
    return {"group_by": group_by, "total": len(rows), "rows": out[:top_n]}


# ---------------------------------------------------------------- 聚合 ④ 归因来源

def attribution_source_ratio(rows: Iterable[Any]) -> dict:
    """归因来源占比（/metricsz 匿名指标）。

    deterministic_ratio = map/(map+llm+unknown)，是可对外讲的"确定性归因"质量数字。
    """
    counts: Dict[str, int] = {s: 0 for s in ATTRIBUTION_SOURCES}
    total = 0
    for r in rows or []:
        counts[_normalize_source(_get(r, "attribution_source"))] += 1
        total += 1
    return {
        "total": total,
        "counts": dict(counts),
        "ratios": {s: _ratio(counts[s], total) for s in ATTRIBUTION_SOURCES},
        "deterministic_ratio": _ratio(counts["map"], total),
        "unknown_ratio": _ratio(counts["unknown"], total),
    }


def metrics_payload(rows: Iterable[Any]) -> dict:
    """/metricsz 的匿名归因指标（不含任何 student_id / 题干等个人信息）。"""
    src = attribution_source_ratio(rows)
    dist = count_by_error_type(rows)
    return {
        "attribution_total": src["total"],
        "attribution_source_counts": src["counts"],
        "attribution_source_ratios": src["ratios"],
        "deterministic_ratio": src["deterministic_ratio"],
        "unknown_ratio": src["unknown_ratio"],
        "error_type_counts": {d["error_type"]: d["count"] for d in dist},
    }


# ---------------------------------------------------------------- 错题本 correction

def pick_correction_detail(step: Any, error_type: Any = None) -> tuple:
    """从步骤的归因映射里挑一条可直接展示的"正确做法"，返回 (文本, 是否按错因精确命中)。

    支持三种映射结构（见 p0_attribution_maps.sql）：
      - typical_errors: [{"error_type","reason","correction","evidence_hint"}]
      - distractor_map: {"wrong_step","error_type","reason","correction"}
      - wrong_value_map: [{"value","wrong_step","error_type","reason","correction"}]
    优先取与 error_type 匹配的那条；**取不到就退化为第一条**（有参考做法总比空白强），
    但 matched=False 会一并返回，展示侧可据此改文案，避免把"参考做法"说成"针对你这个错因"。
    """
    if step is None:
        return None, False
    want = normalize_error_type(error_type) if error_type else None

    def _corr_of(item: Any) -> Optional[str]:
        if not isinstance(item, dict):
            return None
        c = (item.get("correction") or "").strip()
        return c or None

    def _items_match(items: Any) -> tuple:
        if not isinstance(items, list):
            return None, False
        if want:
            for it in items:
                if isinstance(it, dict) and normalize_error_type(it.get("error_type")) == want:
                    c = _corr_of(it)
                    if c:
                        return c, True
        for it in items:
            c = _corr_of(it)
            if c:
                return c, False
        return None, False

    c, ok = _items_match(_get(step, "typical_errors"))
    if c:
        return c, ok
    dm = _get(step, "distractor_map")
    if isinstance(dm, dict):
        if (not want) or normalize_error_type(dm.get("error_type")) == want or want == UNKNOWN:
            c = _corr_of(dm)
            if c:
                return c, True
    c, ok = _items_match(_get(step, "wrong_value_map"))
    if c:
        return c, ok
    # 兜底：步骤的常见错误原因也值得展示（至少告诉学生这一步容易错在哪）
    er = (_get(step, "error_reason") or "").strip()
    return (er or None), False


def pick_correction(step: Any, error_type: Any = None) -> Optional[str]:
    """只要文本的场景用这个（pick_correction_detail 的薄包装）。"""
    return pick_correction_detail(step, error_type)[0]


def build_wrong_question_enrich(
    step: Any,
    error_type: Any = None,
    kp_card: Any = None,
) -> dict:
    """错题本增强信息：错因中文名 + 该步正确做法 + 知识点卡（understand/memory）。"""
    t = normalize_error_type(error_type)
    correction, matched = pick_correction_detail(step, t)
    return {
        "error_type": t,
        "error_type_label": ERROR_TYPE_LABELS.get(t, ERROR_TYPE_LABELS[UNKNOWN]),
        "step_text": (step is not None and (_get(step, "text") or "")) or None,
        "step_label": (step is not None and (_get(step, "label") or "")) or None,
        "kp_name": (step is not None and (_get(step, "kp_name") or "")) or None,
        "correction": correction,
        "correction_matched": matched,  # False=该步没有针对这个错因的做法，展示的是通用参考做法
        "explanation": (step is not None and (_get(step, "explanation") or "")) or None,
        "understanding": (kp_card is not None and (_get(kp_card, "understanding") or "")) or None,
        "memory": (kp_card is not None and (_get(kp_card, "memory") or "")) or None,
        "advice": advice_of(t),
    }


# ---------------------------------------------------------------- CSV 导出

def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """生成 CSV 文本（纯文本，不带 BOM；调用方若要给 Excel 直开需自行补 \\ufeff）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(list(header))
    for r in rows or []:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def distribution_csv(dist: dict) -> str:
    """把 build_class_distribution 的结果转成教研会可直接用的 CSV。"""
    group_by = dist.get("group_by", "error_type")
    if group_by == "error_type":
        header = ["错因类型", "错因名称", "次数", "占比", "针对性建议"]
        rows = [
            [r["key"], r["label"], r["count"], f"{r['ratio']:.1%}", r.get("advice", "")]
            for r in dist.get("rows", [])
        ]
    elif group_by == "question":
        header = ["题目(content_hash)", "题干", "出错次数", "占比", "主要错因", "涉及学生数"]
        rows = [
            [r["key"], r["label"], r["count"], f"{r['ratio']:.1%}",
             r.get("main_error_label", ""), r.get("student_count", 0)]
            for r in dist.get("rows", [])
        ]
    else:
        header = ["题目(content_hash)", "步骤", "步骤内容", "出错次数", "占比", "主要错因", "涉及学生数"]
        rows = [
            [r.get("content_hash", ""), r.get("step_no", 0), r["label"], r["count"],
             f"{r['ratio']:.1%}", r.get("main_error_label", ""), r.get("student_count", 0)]
            for r in dist.get("rows", [])
        ]
    return to_csv(header, rows)
