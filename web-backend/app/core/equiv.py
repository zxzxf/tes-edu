"""确定性等价判定（MVP 作业闭环判分）。

0 token 纯函数，无 LLM / DB 依赖。把选择题 / 填空题的「答案等价」判定固化，
使对错完全可确定性化（判分唯一来源是『本卷答案』，不调 LLM）。

移植自 agent-backend/app/core/equiv.py（已上线、gold set 回归门禁覆盖）。
与 judge_v2 的 _norm_value / _blank_match / _ans_values 归一化口径一致。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Mapping

# ---------------------------------------------------------------- 归一化
_TRANS = {
    "×": "*", "✕": "*", "·": "*", "÷": "/", "－": "-", "—": "-",
    "−": "-", "＋": "+", "＝": "=", "（": "(", "）": ")", "，": ",",
}
_LEAD = re.compile(
    r"^(等于|答案为|答案|结果是|结果|答|是|应选|选了|选|填写|填|为|：|:)"
)


def norm_text(s: str) -> str:
    """答案归一化：NFKC 统一全角、统一异体运算符、去 LaTeX 装饰与首尾噪声。

    刻意**不删中间标点**（括号/逗号/小数点/冒号保留），否则会破坏小数(2.44)、
    坐标(2,3)、带变量名(E(X)=...) 等答案的等价比较。
    """
    s = (s or "").strip()
    s = unicodedata.normalize("NFKC", s).lower()
    for k, v in _TRANS.items():
        s = s.replace(k, v)
    s = s.replace("$", "").replace("\\left", "").replace("\\right", "")
    s = re.sub(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", s)
    return s.strip("。. \t\r\n")


def strip_lead(s: str) -> str:
    """剥掉作答里的中文引导词（最多两轮，覆盖「答案为选 A」叠写）。"""
    for _ in range(2):
        s2 = _LEAD.sub("", s)
        if s2 == s:
            break
        s = s2
    return s.strip()


# ---------------------------------------------------------------- 数值等价
_FRAC = re.compile(r"^\(?(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\)?$")


def to_number(s: str):
    """把 ``61/25`` / ``2.44`` / ``-1/2`` 等解析为 float；解析不了返回 None。
    分数与小数交叉等价：``to_number('61/25') == to_number('2.44')``。"""
    s = norm_text(s)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _FRAC.match(s)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return num / den if den != 0 else None
    return None


def values_equivalent(a: str, b: str) -> bool:
    """两答案值是否等价：归一字符串相等，或数值相等（分数/小数交叉）。"""
    if norm_text(a) == norm_text(b):
        return True
    va, vb = to_number(a), to_number(b)
    if va is not None and vb is not None:
        return abs(va - vb) < 1e-9
    return False


# ---------------------------------------------------------------- 多解展开
_MULTI = re.compile(r"[、,，;；]|\s*(?:或|和|与)\s*")


def split_multi(s: str) -> list[str]:
    """把多解答案拆成值列表：``2 或 -2`` -> ['2','-2']；``±2`` -> ['2','-2']。
    无多解返回 [s]。"""
    s = strip_lead(s)
    out: list[str] = []
    for part in _MULTI.split(s or ""):
        p = part.strip()
        if not p:
            continue
        if p.startswith("±") or p.startswith("+-") or p.startswith("∓"):
            v = p[1:].lstrip("+-")
            if v:
                out += [v, "-" + v]
        else:
            out.append(p)
    return out or [s]


# ---------------------------------------------------------------- 选择题
def norm_choice_label(s: str):
    """``C`` / ``c`` / ``C. 12`` / ``C.12`` -> ``C``；非法返回 None。"""
    s = norm_text(s)
    if not s:
        return None
    m = re.match(r"^([a-z])\b", s)
    return m.group(1).upper() if m else None


def _choice_labels(x) -> set:
    if isinstance(x, (set, list, tuple, Iterable)) and not isinstance(x, str):
        items = list(x)
    else:
        items = [p.strip() for p in re.split(r"[,，;；/]", str(x)) if p.strip()]
    out = set()
    for it in items:
        s = norm_text(it)
        # 多字母连写（如 'ACD' / 'a,c,d'）拆成 A C D —— 题库多选答案常无分隔符存成 'ACD'
        if len(s) >= 2 and all(c in "abcd" for c in s):
            out.update(c.upper() for c in s)
            continue
        lab = norm_choice_label(s)
        if lab:
            out.add(lab)
    return out


def choice_equivalent(student, ref, multi: bool = False, options: Mapping | None = None):
    """选择题等价判定。

    ``student`` / ``ref``：标签字符串（``'C'`` / ``'A,B'``）或标签集合/列表。
    ``options``：可选 ``{label: content}`` 映射，用于识别「学生只写数值」的选法
    （如本题选 C 而选项内容恰为 12，则学生写 ``12`` 等价于选 C）。
    返回 ``True``（全对）/ ``False``（错）/ ``'partial'``（多选漏选但所选皆正确）。
    """
    rl = _choice_labels(ref)
    if not rl:
        return False
    sl = _choice_labels(student)
    if sl == rl:
        return True
    # 学生写数值对应某选项内容 -> 当 ref 单标签且内容匹配时判对
    if options and len(rl) == 1:
        ref_content = str(options.get(next(iter(rl)), "")).strip()
        if ref_content and values_equivalent(str(student), ref_content):
            return True
    if multi and sl and sl < rl:
        return "partial"
    return False


# ---------------------------------------------------------------- 填空
def _var_candidates(s: str) -> list[str]:
    """含等号时取等号右侧段作为候选（``a=1`` -> ['a=1','1']），否则原样。"""
    s = (s or "").strip()
    if "=" in s or "＝" in s:
        rhs = re.split(r"[=＝]", s)[-1].strip()
        return [s, rhs] if rhs else [s]
    return [s]


def _blank_single(student: str, ref: str) -> bool:
    """单空等价：归一相等 | 数值等价 | 带前缀/多解求交。

    红线：只能「学生候选 vs 标答值集合」求交；绝不把标答自己的候选当匹配键。
    """
    if values_equivalent(student, ref):
        return True
    ref_vals: set = set()
    for c in split_multi(ref):
        ref_vals.update(_var_candidates(c))
        ref_vals.add(strip_lead(c))
    stu_vals: set = set()
    for c in split_multi(student):
        stu_vals.update(_var_candidates(c))
        stu_vals.add(strip_lead(c))
    for sv in stu_vals:
        for rv in ref_vals:
            if values_equivalent(sv, rv):
                return True
    return False


def blank_equivalent(student: str, ref: str) -> bool:
    """填空逐空等价 + 多解展开。"""
    if not ref:
        return False
    ref_parts = [p for p in re.split(r"[;；,，]", ref) if p.strip()]
    if len(ref_parts) > 1:
        stu_parts = [p for p in re.split(r"[;；,，]", student) if p.strip()]
        if len(stu_parts) != len(ref_parts):
            return False
        return all(_blank_single(a, b) for a, b in zip(stu_parts, ref_parts))
    return _blank_single(student, ref)
