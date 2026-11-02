"""数值一致性护栏（M2-11）。

**只做一件事**：当系统已有**可信标准答案**（本卷答案 / 题库标注答案）时，
保证解析与归因文字里的"结论数值"与之不冲突。

边界（宁可不管，不可改错）：
* 只处理**标量数值**（``61/25``、``2.44``、``-1/2``、``\\frac{61}{25}``）；
  ``±2``、``a=1``、``x>0``、区间 ``(2,3)`` 一律视为不可判定，直接放过。
* 只认为「答案为 / 答案是 / 故选 / 应选 / 结论为 / 得 …」之后的数值是**结论**；
  推导过程中的中间值（``60/25``）不是结论，不能当冲突。
* 没有可信标答时**永不**判冲突、**永不**改字。

为什么不复用 ``orchestrator._sanitize_comment_numbers``：那个只认
「（或/即/等于 + 数字）」这一种最易被编造的形态，且只用于点评；解析里更常见的
是「所以答案为 3.04」这种整句结论，需要更强一点、但仍然保守的识别。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# 允许出现在数字前的正负号与常见 LaTeX 包装
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?")
_FRAC_RE = re.compile(r"\\[dt]?frac\{(-?\d+(?:\.\d+)?)\}\{(-?\d+(?:\.\d+)?)\}")


def parse_number(raw: Any) -> Optional[float]:
    """把标量数值解析成 float；任何带符号/区间/变量的写法返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("$", "").replace("，", "").replace(",", "").strip()
    m = _FRAC_RE.fullmatch(s)
    if m:
        den = float(m.group(2))
        return float(m.group(1)) / den if den else None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?(?:/-?\d+(?:\.\d+)?)?", s):
        return None
    if "/" in s:
        a, b = s.split("/", 1)
        b_f = float(b)
        return float(a) / b_f if b_f else None
    return float(s)


def answer_values(raw: Any) -> List[float]:
    """标答展开成可比较的数值集合：支持 ``2 或 -2``、``±2``、``{2,-2}``。

    非数值标答（``A``、``x=1``）返回空列表——调用方据此跳过护栏。
    """
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    s = s.replace("$", "").replace("　", " ")

    one = parse_number(s)
    if one is not None:
        return [one]

    # ±2 / +-2 / 正负2
    m = re.fullmatch(r"(?:±|\+-|正负)\s*(-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)", s)
    if m:
        v = parse_number(m.group(1))
        return [v, -v] if v is not None else []

    parts = re.split(r"或|,|，|、|;|；|\s+and\s+|和", s)
    vals: List[float] = []
    for p in parts:
        p = p.strip().strip("{}[]()（）").strip()
        if not p:
            continue
        v = parse_number(p)
        if v is None:
            return []  # 有一项不是纯数值 -> 整条标答按"不可判定"处理
        vals.append(v)
    return vals


#: 结论前缀：只有紧跟其后的数值才被当作"本题最终答案"
_CLAIM_RE = re.compile(
    r"(?:答案(?:为|是|：|:)?|故选|应选|结论(?:为|是)?|最终(?:答案|结果)?(?:为|是)?|"
    r"正确答案(?:为|是)?|所以[^。；\n]{0,12}?(?:为|是|得))\s*"
    r"([^\s。；;，,、]{1,24})"
)

#: 在这些字符之后出现的数字属于推导过程，不是结论
_STEP_MARKERS = ("=", "＋", "+", "×", "*", "÷", "/", "(", "（", "[", "【")


def _similar(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def conflict_values(text: Optional[str], reference: Any) -> List[float]:
    """返回 ``text`` 里与标答冲突的结论数值（空列表 = 无冲突/不可判定）。"""
    trusted = answer_values(reference)
    if not trusted or not text:
        return []
    out: List[float] = []
    for m in _CLAIM_RE.finditer(text):
        frag = m.group(1).strip().strip("。；;，,、)）]】")
        # 「答案不是 3」这类否定句不是结论数值
        if frag.startswith("不"):
            continue
        v = parse_number(frag)
        if v is None:
            # 「答案为 2 或 -2」这种多解写法：整串解析，任一值不在标答里才算冲突
            vals = answer_values(frag)
            if not vals:
                continue
            for x in vals:
                if not any(_similar(x, t) for t in trusted):
                    out.append(x)
                    break
            continue
        if not any(_similar(v, t) for t in trusted):
            out.append(v)
    return out


def conflicting_claims(text: Optional[str], reference: Any) -> List[float]:
    """``conflict_values`` 的别名（测试/调用方语义更直白）。"""
    return conflict_values(text, reference)


def _fmt(v: float) -> str:
    return f"{v:.10g}"


def _rewrite(text: str, wrong: float, right: float) -> str:
    """替换紧跟结论前缀的**所有**同值数值，不动推导里的其它数字。

    同一份解析里结论可能重复出现（answer 与 steps 各一次、或同段多句），
    只改第一处会留下"前面对、后面错"的自相矛盾。
    """
    pattern = re.compile(
        r"((?:答案(?:为|是|：|:)?|故选|应选|结论(?:为|是)?|最终(?:答案|结果)?(?:为|是)?|"
        r"正确答案(?:为|是)?|所以[^。；\n]{0,12}?(?:为|是|得))\s*)"
        + re.escape(_fmt(wrong))
    )
    return pattern.sub(lambda m: m.group(1) + _fmt(right), text)


def guard_analysis(result: Dict[str, Any], reference: Any) -> Dict[str, Any]:
    """解析护栏：``{"answer": ..., "steps": [...]}`` 就地纠正冲突数值。

    行为约定：
    * 有冲突且能安全替换 -> 改字，``numeric_conflict=False``；
    * 有冲突但替换不安全 -> 保留原文，``numeric_conflict=True``（调用方可提示核对）；
    * 无可信标答 -> 原样返回，``numeric_conflict=False``。
    """
    trusted = answer_values(reference)
    out: Dict[str, Any] = dict(result or {})
    if not trusted:
        out["numeric_conflict"] = False
        return out

    conflicts: List[float] = []
    for key in ("answer", "steps"):
        val = out.get(key)
        items = val if isinstance(val, list) else ([val] if isinstance(val, str) else [])
        for item in items:
            if isinstance(item, str):
                conflicts.extend(conflict_values(item, reference))

    if not conflicts:
        out["numeric_conflict"] = False
        return out

    right = trusted[0]
    rewritten = False
    for key in ("answer", "steps"):
        val = out.get(key)
        if isinstance(val, str):
            new = val
            for wrong in conflicts:
                new = _rewrite(new, wrong, right)
            if new != val:
                out[key] = new
                rewritten = True
        elif isinstance(val, list):
            items = []
            for item in val:
                if isinstance(item, str):
                    new = item
                    for wrong in conflicts:
                        new = _rewrite(new, wrong, right)
                    if new != item:
                        rewritten = True
                    items.append(new)
                else:
                    items.append(item)
            out[key] = items

    remaining: List[float] = []
    for key in ("answer", "steps"):
        val = out.get(key)
        items = val if isinstance(val, list) else ([val] if isinstance(val, str) else [])
        for item in items:
            if isinstance(item, str):
                remaining.extend(conflict_values(item, reference))

    out["numeric_conflict"] = bool(remaining) or not rewritten
    out["numeric_reference"] = str(reference)
    return out


def needs_review(text: Optional[str], reference: Any) -> bool:
    """仅判定是否需要人工核对（不改内容）。"""
    return bool(conflict_values(text, reference))
