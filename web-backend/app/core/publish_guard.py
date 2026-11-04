"""M4-6 答案必填 + M4-7 G2 新卷抽样复核（纯函数 + 发布闸门接线）。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.core.equiv import blank_judge, choice_equivalent
from app.core.exam_types import QTYPE_BLANK, QTYPE_CHOICE, QTYPE_SUBJECTIVE


def _get(q: Any, key: str, default=None):
    return q.get(key, default) if isinstance(q, dict) else getattr(q, key, default)


def missing_scores(questions: Sequence[Any]) -> List[Dict[str, Any]]:
    """M4-6：发布前逐题确认「分值已配」。

    分值为空不是「默认 5 分」这种可猜的事：总分要进成绩单，猜错就是改分事故。
    规则组卷会自动配分，手动出卷必须由教师显式填。
    """
    out = []
    for q in questions or []:
        score = _get(q, "score")
        if score is None or float(score) <= 0:
            out.append({
                "seq": int(_get(q, "seq", 0) or 0),
                "qtype": str(_get(q, "qtype", "") or ""),
                "code": "SCORE_MISSING",
                "message": f"第 {_get(q, 'seq', 0)} 题未配分，无法计算总分",
            })
    return out


def missing_answers(questions: Sequence[Any]) -> List[Dict[str, Any]]:
    """M4-6：无答案的题不允许发布（选择题空答案会被结构校验拦，这里兜底全题型）。"""
    out = []
    for q in questions or []:
        if not str(_get(q, "answer", "") or "").strip():
            out.append({
                "seq": int(_get(q, "seq", 0) or 0),
                "qtype": str(_get(q, "qtype", "") or ""),
                "code": "ANSWER_MISSING",
                "message": f"第 {_get(q, 'seq', 0)} 题没有答案，无法判分",
            })
    return out


def _options_map(options: Any) -> Dict[str, str]:
    if isinstance(options, dict):
        return {str(k): str(v) for k, v in options.items()}
    if isinstance(options, list):
        out = {}
        for i, item in enumerate(options):
            if isinstance(item, dict):
                lab = str(item.get("label") or item.get("key") or "").strip()
                content = str(item.get("text") or item.get("value") or "")
            else:
                lab = chr(ord("A") + i)
                content = str(item)
            if lab:
                out[lab] = content
        return out
    return {}


def _wrong_choice(answer: str, options: Dict[str, str]) -> Optional[str]:
    """构造一个**必然判错**的选择题答案（存在第二个合法选项时）。"""
    labels = [l for l in options if l]
    if len(labels) < 2:
        return None
    right = {l.upper() for l in str(answer or "") if l.isalpha()}
    for lab in labels:
        if lab.upper() not in right:
            return lab
    return None


def sample_verdicts(question: Any) -> Dict[str, Any]:
    """M4-7 G2：对单题跑四类标准样本，返回判定分布。

    样本与判据必须走**与线上提交完全相同的函数**（choice_equivalent / blank_judge），
    否则抽样只是在验一套影子逻辑，和真实判分分叉毫无意义。
    """
    qtype = str(_get(question, "qtype", "") or "")
    answer = str(_get(question, "answer", "") or "")
    multi = bool(_get(question, "multi", False))
    options = _options_map(_get(question, "options"))
    values = list(_get(question, "answer_values", None) or [])

    out = {
        "seq": int(_get(question, "seq", 0) or 0),
        "qtype": qtype,
        "correct_solution": None,   # True=判对 / False=判错 / None=转人工
        "common_error": None,
        "equivalent_form": None,
        "boundary_form": None,
        "problems": [],
    }
    if qtype == QTYPE_CHOICE:
        # 标答指向的选项必须真实存在：结构校验也会拦，但抽样要能独立发现
        # （否则「换个入口发布」就能绕过这道检查）
        labels = {c.upper() for c in answer if c.isalpha()}
        absent = sorted(l for l in labels if l not in {k.upper() for k in options})
        if absent:
            out["problems"].append(f"标答 {'/'.join(absent)} 不在选项内")
        res = choice_equivalent(answer, answer, multi=multi, options=options)
        out["correct_solution"] = True if res is True else (None if res == "manual" else False)
        wrong = _wrong_choice(answer, options)
        if wrong is not None:
            out["common_error"] = choice_equivalent(
                wrong, answer, multi=multi, options=options
            ) is True
        # 等价写法：小写字母是同一选项（学生输入大小写不该改变判定）
        if answer.strip().isalpha():
            out["equivalent_form"] = choice_equivalent(
                answer.strip().lower(), answer, multi=multi, options=options
            ) is True
        out["boundary_form"] = choice_equivalent(
            "", answer, multi=multi, options=options
        ) is True
    elif qtype == QTYPE_BLANK:
        v = blank_judge(answer, answer, values=values or None)
        out["correct_solution"] = True if v["status"] == "auto" else None
        if out["correct_solution"] is None:
            out["problems"].append("标准答案自身被判为转人工（标答形态不可自动判定）")
        # 常见错法：故意改一个数字；不适用时视为已覆盖（不阻断）
        altered = _alter_number(answer)
        if altered is not None:
            wrong = blank_judge(altered, answer, values=values or None)
            out["common_error"] = wrong.get("correct") is True
        out["equivalent_form"] = blank_judge(
            f" {answer} ", answer, values=values or None
        ).get("correct") is True
        out["boundary_form"] = blank_judge("", answer, values=values or None).get(
            "correct"
        ) is True
    elif qtype == QTYPE_SUBJECTIVE:
        # 解答题一律人工，抽样不适用（发布时也只标「需人工 N 道」）
        return out
    return out


def _alter_number(text: str) -> Optional[str]:
    """把答案里第一个整数 ±1，构造一个「常见错法」样本；无数字返回 None。"""
    import re

    m = re.search(r"-?\d+", text or "")
    if not m:
        return None
    value = int(m.group(0))
    # 只改一个数字位（10 -> 11、-3 -> -4），避免改成「+1」这类与原文不同形的写法
    changed = str(value + 1) if value >= 0 else str(value - 1)
    return text[: m.start()] + changed + text[m.end():]


def sample_report(questions: Sequence[Any]) -> Dict[str, Any]:
    """M4-7：整卷抽样结论。返回 ``{"ok": bool, "samples": [...], "blocking": [...]}``。"""
    samples = [sample_verdicts(q) for q in (questions or [])]
    blocking: List[Dict[str, Any]] = []
    for s in samples:
        if s["qtype"] == QTYPE_SUBJECTIVE:
            continue
        problems = list(s.get("problems") or [])
        if s["correct_solution"] is False:
            problems.append("正确解被判错")
        if s["correct_solution"] is None and s["qtype"] == QTYPE_CHOICE:
            problems.append("正确解无法自动判定（转人工）")
        if s["common_error"] is True:
            problems.append("常见错法被判对")
        if s["equivalent_form"] is False:
            problems.append("等价写法被判错")
        if s["boundary_form"] is True:
            problems.append("空答案被判对")
        for p in problems:
            blocking.append({
                "seq": s["seq"], "qtype": s["qtype"],
                "code": "G2_SAMPLE_FAILED", "message": p,
            })
    return {"ok": not blocking, "samples": samples, "blocking": blocking}