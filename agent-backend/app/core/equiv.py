"""确定性等价判定（作业闭环判分，M2-1/2/3）。

0 token 纯函数，无 LLM / DB 依赖。把选择题 / 填空题的「答案等价」判定固化，
使判分唯一来源是『本卷答案快照』，不调 LLM、不查外部检索。

web-backend 与 agent-backend **两份文件逐字一致**（web 端提交判分、agent 端
judge_v2 路径⓪/② 自洽校验共用一套口径）。改动必须同时落到两端，
否则同一道题会在「学生端提交判分」和「agent 自洽校验」两个入口判出不同结果。

M2-3 的三条红线
---------------
1. **解析不了的结构绝不硬判错**：含变量表达式、符号函数（ln2）、方程等无法数值化的
   答案，归一化后字符串相等才判对，不等一律 ``manual``（转人工）。成绩错了不可逆，
   转人工只是多一道手续。
2. **多空 / 多解切分必须在括号外**：``[2,3)`` 与 ``(-∞,-4)∪(0,+∞)`` 内部的逗号
   不能当分隔符，否则会切成 ``[2`` / ``3)`` 造成静默误判。
3. **带变量名只取简单变量**：``x = -3/2`` 可取右侧 ``-3/2``；``3x+4y-5=0`` 是方程，
   **不能**取右侧的 ``0``（否则学生写 0 就被判对）。

对外 API
--------
- ``blank_judge(student, ref, blanks=None) -> dict``：三态判定，web 提交判分用。
  ``{"status": "auto"|"manual", "correct": bool|None, "reason": str}``
- ``blank_equivalent(student, ref) -> bool``：兼容语义（只表达「是否判对」），
  agent judge_v2 与历史调用方使用；``manual`` 返回 False 交 LLM 兜底，不会误判错。
- ``blank_auto_gradeable(ref) -> bool``：该标答能否确定性判定（发布时统计人工量用）。
- ``expand_answer_values(ref) -> List[str]``：标答预展开成可接受答案值集合（M2-4），
  组卷时落库、教师批改时可追加，判分时命中即判对。
"""
from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

# ---------------------------------------------------------------- 归一化
_TRANS = {
    "×": "*", "✕": "*", "·": "*", "÷": "/", "－": "-", "—": "-",
    "−": "-", "＋": "+", "＝": "=", "（": "(", "）": ")", "，": ",",
    "；": ";", "：": ":", "∪": "|", "∓": "±",
}
# 希腊字母 / 数学常量 -> ASCII 名（含 LaTeX 写法）
_GREEK = {
    "π": "pi", "θ": "theta", "α": "alpha", "β": "beta", "γ": "gamma",
    "δ": "delta", "λ": "lambda", "μ": "mu", "σ": "sigma", "φ": "phi",
    "ω": "omega", "∞": "inf",
}
_LATEX_GREEK = re.compile(
    r"\\(pi|theta|alpha|beta|gamma|delta|lambda|mu|sigma|phi|omega|infty)\b"
)
_LATEX_INFINITY = {"infty": "inf"}
_LEAD = re.compile(
    r"^(等于|答案为|答案|结果是|结果|答|是|应选|选了|选|填写|填|为|：|:)"
)

_FRAC_IN = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
# LaTeX 简写：\dfrac32 / \sqrt3 / \sqrt[3]3（题库真实素材里有，无花括号）
_FRAC_PLAIN = re.compile(r"\\[dt]?frac\s*(\d+(?:\.\d+)?)\s*(\d+(?:\.\d+)?)")
_SQRT_N = re.compile(r"\\sqrt\s*\[\s*(\d+)\s*\]\s*\{([^{}]*)\}")
_SQRT_N_PLAIN = re.compile(r"\\sqrt\s*\[\s*(\d+)\s*\]\s*(\d+(?:\.\d+)?)")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_SQRT_PLAIN = re.compile(r"\\sqrt\s*(\d+(?:\.\d+)?)")
_OR_OPEN = re.compile(r"\(\s*(?:或|和|与)")


def norm_text(s: str) -> str:
    """答案归一化：NFKC 统一全角、LaTeX 去壳、数学符号转 ASCII、去首尾噪声。

    刻意**不删中间标点**（括号/逗号/小数点/冒号保留），否则会破坏小数(2.44)、
    坐标(2,3)、区间 ``[2,3)``、带变量名 ``E(X)=...`` 等答案的等价比较。
    """
    text = (s or "").strip()
    text = unicodedata.normalize("NFKC", text).lower()
    for k, v in _TRANS.items():
        text = text.replace(k, v)
    text = text.replace("$", "").replace("\\left", "").replace("\\right", "")
    # LaTeX 间距命令（\! \, \; \ 等）不影响值
    text = re.sub(r"\\[,;:! ]", "", text)
    # 嵌套结构逐层剥开：frac 与 sqrt 可能互相包裹，循环到不再变化
    for _ in range(5):
        before = text
        text = _SQRT_N.sub(lambda m: f"nrt({m.group(2)},{m.group(1)})", text)
        text = _SQRT_N_PLAIN.sub(lambda m: f"nrt({m.group(2)},{m.group(1)})", text)
        text = _SQRT.sub(r"sqrt(\1)", text)
        text = _SQRT_PLAIN.sub(r"sqrt(\1)", text)
        text = _FRAC_IN.sub(r"(\1)/(\2)", text)
        text = _FRAC_PLAIN.sub(r"(\1)/(\2)", text)
        if text == before:
            break
    text = _LATEX_GREEK.sub(lambda m: _LATEX_INFINITY.get(m.group(1), m.group(1)), text)
    for k, v in _GREEK.items():
        text = text.replace(k, v)
    text = text.replace("\\", "")            # 残留 LaTeX 反斜杠（\theta 等已先转）
    # √ 简写：√6 -> sqrt(6)，√(...) -> sqrt(...)
    text = re.sub(r"√\s*(\d+(?:\.\d+)?)", r"sqrt(\1)", text)
    text = text.replace("√", "sqrt")
    # 函数简写：ln2 -> ln(2) / sqrt6 -> sqrt(6)（数学语境下无歧义，便于数值化）
    text = re.sub(r"\b(ln|log|exp|sin|cos|tan|sqrt|nrt)\s*(\d+(?:\.\d+)?)", r"\1(\2)", text)
    text = text.replace("^", "**")
    return text.strip("。. \t\r\n")


def strip_lead(s: str) -> str:
    """剥掉作答里的中文引导词（最多两轮，覆盖「答案为选 A」叠写）。"""
    for _ in range(2):
        s2 = _LEAD.sub("", s)
        if s2 == s:
            break
        s = s2
    return s.strip()


# ---------------------------------------------------------------- 数值等价
# 受限 AST 求值：只允许四则/幂/白名单函数与常量，绝不用 eval（防注入）。
_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _nrt(x: float, n: float) -> float:
    if n == 0:
        raise ZeroDivisionError
    if x < 0 and n % 2 == 0:
        raise ValueError("even root of negative")
    return x ** (1.0 / n)


_FUNCS = {
    "sqrt": math.sqrt, "nrt": _nrt, "abs": abs,
    "ln": math.log, "log": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
}
_NAMES = {"pi": math.pi, "e": math.e}


def _safe_eval(node) -> Optional[float]:
    """递归求值白名单 AST；任何未知节点返回 None（=> 转人工，不判错）。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in _NAMES:
        return float(_NAMES[node.id])
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARYOPS:
        v = _safe_eval(node.operand)
        return None if v is None else _UNARYOPS[type(node.op)](v)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        a, b = _safe_eval(node.left), _safe_eval(node.right)
        if a is None or b is None:
            return None
        try:
            r = _BINOPS[type(node.op)](a, b)
        except (ZeroDivisionError, OverflowError, ValueError):
            return None
        return float(r) if not isinstance(r, complex) else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and 1 <= len(node.args) <= 2
        and not node.keywords
    ):
        args = [_safe_eval(a) for a in node.args]
        if any(a is None for a in args):
            return None
        try:
            return float(_FUNCS[node.func.id](*args))
        except (ValueError, OverflowError, ZeroDivisionError, TypeError):
            return None
    return None


def _to_expr(s: str) -> str:
    """补隐式乘号，让 ``7sqrt(6)/6`` / ``2pi`` / ``3(4)`` 能被 ast.parse 识别。"""
    t = re.sub(r"(\d|\))\s*(sqrt\(|nrt\(|[A-Za-z_]\w*\(|\()", r"\1*\2", s)
    t = re.sub(r"(\d)\s*(pi|e)\b", r"\1*\2", t)
    return t


_FRAC = re.compile(r"^\(?(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\)?$")


def to_number(s: str) -> Optional[float]:
    """把 ``61/25`` / ``2.44`` / ``7√6/6`` / ``\\dfrac{61}{25}`` / ``3π/2``
    / ``\\sqrt[3]{3/2}`` 解析为 float；解析不了返回 None（调用方应转人工）。"""
    t = norm_text(s)
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        pass
    m = _FRAC.match(t)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return num / den if den != 0 else None
    try:
        tree = ast.parse(_to_expr(t), mode="eval")
    except (SyntaxError, ValueError):
        return None
    return _safe_eval(tree)


# 数值容差：学生手算根式/分数常只写 6~7 位小数（如 7√6/6 写 2.857738），
# 用 1e-9 会把近似值误判为错（判错不可逆）。绝对 1e-6 + 相对 1e-6：
# 既能容纳手写近似，又不会把 2.5 与 2.44 放成同一答案。
_NUM_ABS_TOL = 1e-6
_NUM_REL_TOL = 1e-6


def _num_eq(a: float, b: float) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return a == b
    return abs(a - b) <= max(_NUM_ABS_TOL, _NUM_REL_TOL * max(abs(a), abs(b)))


def values_equivalent(a: str, b: str) -> bool:
    """两答案值是否等价：归一字符串相等，或数值相等（分数/小数交叉）。"""
    if norm_text(a) == norm_text(b):
        return True
    va, vb = to_number(a), to_number(b)
    if va is not None and vb is not None:
        return _num_eq(va, vb)
    return False


_EQ_RHS = re.compile(
    r"^([A-Za-z][A-Za-z0-9]*|[A-Za-z][A-Za-z0-9]*\s*\([^()]*\))\s*=\s*(.+)$"
)


def _var_candidates(s: str) -> List[str]:
    """带等号时取右侧值作为候选（``x = -3/2`` -> ['x = -3/2', '-3/2']）。

    **只认左侧是「单个变量名」或「函数记号」**（``E(X)=61/25`` / ``f(x)=x+2``）：

    - ``3x+4y-5=0`` 这类方程不取右侧的 ``0`` —— 否则学生写 0 就判对（题库第 6 题）；
    - ``x^2=4`` 也不取右侧 ``4`` —— 该方程的答案是 x=±2，不是 4。
    """
    s = (s or "").strip()
    m = _EQ_RHS.match(s)
    if m:
        return [s, m.group(2).strip()]
    return [s]


def _num_candidates(s: str) -> List[float]:
    out: List[float] = []
    for c in _var_candidates(s):
        n = to_number(c)
        if n is not None:
            out.append(n)
    return out


# ---------------------------------------------------------------- 顶层切分
def _split_top(s: str, pattern: re.Pattern) -> List[str]:
    """只在**括号外（深度 0）**按 pattern 切分。

    区间 ``[2,3)`` / ``(-∞,-4)`` 内部的逗号、函数 ``f(2π/3)`` 内部的逗号
    都不会被当分隔符 —— 这是 M2-3 不产生静默误判的关键。
    """
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(depth - 1, 0)
        if depth == 0:
            m = pattern.match(s, i)
            if m:
                out.append("".join(buf))
                buf = []
                i = m.end()
                continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


_MULTI_SEP = re.compile(r"[、,，;；]|\s*(?:或|和|与)\s*")
_BLANK_SEP = re.compile(r"[;；,，]")


def _trim_wrap(p: str) -> str:
    """只清理「多解包裹」残留的**孤立**括号，绝不碰配对括号与区间端点。

    ``3x+4y-5=0(`` -> ``3x+4y-5=0``；``sqrt((11)/(6)))`` -> ``sqrt((11)/(6))``。
    但 ``(sqrt(66))/(6)`` / ``[2,3)`` / ``(-∞,-4)`` 必须原样保留 —— 直接
    ``strip('()')`` 会把半开区间右端点 ``)`` 也剥掉，造成静默误判。
    """
    p = p.strip()
    while p.startswith("(") and p.count("(") > p.count(")"):
        p = p[1:].strip()
    while p.endswith(")") and p.count(")") > p.count("("):
        p = p[:-1].strip()
    return p


def _keep_interval(p: str, fn) -> str:
    """区间串原样返回（其首尾括号是端点语法），其余才做包裹清理。"""
    return p if normalize_interval(p) is not None else fn(p)


def _unwrap_or(s: str) -> str:
    """把末尾的「(或 … / 和 … / 与 …)」多解包裹括号展开到顶层。

    输入须先经 :func:`norm_text`（全角括号已转半角、LaTeX 已去壳）。
    ``3x+4y-5=0(或 x=-1 或 7x-24y-25=0)`` -> ``3x+4y-5=0 或 x=-1 或 7x-24y-25=0``
    ``(sqrt(66))/(6)(或 sqrt((11)/(6)))`` -> ``(sqrt(66))/(6) 或 sqrt((11)/(6))``

    用括号配对扫描定位这对包裹括号（而非正则），这样内部嵌套的
    ``sqrt((11)/(6))`` 不会被误当成包裹边界 —— 题库第 19 题正是这种形态。
    """
    n = len(s)
    i = 0
    while i < n:
        if s[i] != "(":
            i += 1
            continue
        j = i + 1
        while j < n and s[j].isspace():
            j += 1
        if j >= n or s[j] not in "或和与":
            i += 1
            continue
        depth, k = 1, i + 1
        while k < n and depth:
            if s[k] == "(":
                depth += 1
            elif s[k] == ")":
                depth -= 1
            k += 1
        if depth == 0 and not s[k:].strip():
            return s[:i] + " " + s[i + 1:k - 1]
        i += 1
    return s


def split_multi(s: str) -> List[str]:
    """把多解答案拆成值列表：``2 或 -2`` -> ['2','-2']；``±2`` -> ['2','-2']。

    **先归一化再切分**：全角 ``（或 …）`` 与 LaTeX 需先转 ASCII，否则包裹
    残留会留在候选里（题 6/19），且嵌套括号内的「或」会漏切。
    无多解返回 [s]。
    """
    s = _unwrap_or(norm_text((s or "").strip()))
    out: List[str] = []
    for part in _split_top(s, _MULTI_SEP):
        p = _keep_interval(part.strip(), _trim_wrap)
        if not p:
            continue
        if p.startswith("±") or p.startswith("+-"):
            v = p[1:].lstrip("+-")
            if v:
                out += [v, "-" + v]
        else:
            out.append(p)
    return out or [s]


def split_blanks(s: str) -> List[str]:
    """多空答案按顶层 ``;`` / ``,`` 切分（区间/函数内部逗号不切）。"""
    t = norm_text((s or "").strip())
    return [
        _keep_interval(p.strip(), _trim_wrap)
        for p in _split_top(t, _BLANK_SEP)
        if p.strip()
    ]


# ---------------------------------------------------------------- 区间
_INTERVAL_SEP = re.compile(r"[|∪Uu]")
_INTERVAL_HINT = re.compile(r"[\[\(][^,]*,")


def _canon_bound(x: str) -> Optional[Tuple[float, str]]:
    x = x.strip().lstrip("+")
    if x in ("inf", "-inf", "+inf"):
        return (math.inf if x != "-inf" else -math.inf, x)
    n = to_number(x)
    if n is None:
        return None
    return (n, f"{n:.12g}")


def normalize_interval(s: str) -> Optional[str]:
    """区间/并集归一化：端点数值化、∞ 统一、并集按左端点排序（交换律）。

    ``(-∞,-4)∪(0,+∞)`` 与 ``(0,+∞)U(-∞,-4)`` -> 同一规范串。
    非区间语法返回 None（交给数值/字符串路径）。
    """
    t = norm_text(s).replace(" ", "")
    if not t or not _INTERVAL_HINT.search(t):
        return None
    segs = [p for p in _INTERVAL_SEP.split(t) if p]
    if not segs:
        return None
    parsed: List[Tuple[float, float, str]] = []
    for seg in segs:
        m = re.match(r"^([\[\(])([^,]+),([^,\]\)]+)([\]\)])$", seg)
        if not m:
            return None
        lbr, lo, hi, rbr = m.group(1), m.group(2), m.group(3), m.group(4)
        lo_c, hi_c = _canon_bound(lo), _canon_bound(hi)
        if lo_c is None or hi_c is None:
            return None
        parsed.append((lo_c[0], hi_c[0], f"{lbr}{lo_c[1]},{hi_c[1]}{rbr}"))
    parsed.sort(key=lambda x: (x[0], x[1]))
    return "|".join(x[2] for x in parsed)


# ---------------------------------------------------------------- 选择题
def norm_choice_label(s: str):
    """``C`` / ``c`` / ``C. 12`` / ``C.12`` -> ``C``；非法返回 None。"""
    s = norm_text(s)
    if not s:
        return None
    m = re.match(r"^([a-z])\b", s)
    return m.group(1).upper() if m else None


def _choice_labels(x) -> Set[str]:
    if isinstance(x, (set, list, tuple, Iterable)) and not isinstance(x, str):
        items = list(x)
    else:
        items = [p.strip() for p in re.split(r"[,，;；/]", str(x)) if p.strip()]
    out: Set[str] = set()
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
    ``options``：可选 ``{label: content}`` 映射，用于识别「学生只写数值」的选法。
    返回 ``True``（全对）/ ``False``（错）/ ``'partial'``（多选漏选但所选皆正确）。
    """
    rl = _choice_labels(ref)
    if not rl:
        return False
    sl = _choice_labels(student)
    if sl == rl:
        return True
    if options and len(rl) == 1:
        ref_content = str(options.get(next(iter(rl)), "")).strip()
        if ref_content and values_equivalent(str(student), ref_content):
            return True
    if multi and sl and sl < rl:
        return "partial"
    return False


# ---------------------------------------------------------------- 填空（三态）
# ``source`` 是「这条结论由哪条判据给出」的机器可读编码（M2-7 指标看板用），
# 与 ``reason`` 分家：reason 是给学生/老师看的话术，改措辞不该动指标；
# source 是给聚合用的枚举，改口径必须改这里（编码见 app/core/grading_metrics.py）。
def _auto(correct: bool, reason: str = "", source: str = "") -> dict:
    return {"status": "auto", "correct": correct, "reason": reason, "source": source}


def _manual(reason: str, source: str = "") -> dict:
    return {"status": "manual", "correct": None, "reason": reason, "source": source}


def _single_value_judge(student: str, ref: str) -> str:
    """单值比较：'correct' / 'wrong' / 'manual'。"""
    s, r = norm_text(student), norm_text(ref)
    if not r:
        return "manual"
    if not s:
        return "wrong"                      # 学生未作答：明确判错
    if s == r:
        return "correct"
    # 数值（含带简单变量名、分数、根式、π）：能双方数值化才可确定性判错
    ss, rs = _num_candidates(s), _num_candidates(r)
    if ss and rs:
        return "correct" if any(_num_eq(a, b) for a in ss for b in rs) else "wrong"
    if values_equivalent(s, r):
        return "correct"
    # 引导词 / 多解前缀候选求交（纯字符串，不引入外部值）
    for sv in [strip_lead(c) for c in split_multi(s)] + _var_candidates(s):
        for rv in [strip_lead(c) for c in split_multi(r)] + _var_candidates(r):
            if values_equivalent(sv, rv):
                return "correct"
    # 一方能数值化、另一方不能 => 可能等价（如 '2.44' vs '61/25' 已覆盖，
    # 但 '0.693' vs 'ln2' 无法证明），不判错
    # 双方都符号化且字符串不等 => 无法证明不等，转人工
    return "manual"


def _judge_single(student: str, ref: str) -> str:
    """单空（可含多解）三态比较。"""
    s = norm_text(student)
    if not s:
        return "wrong"
    # 区间/并集：整体归一比较，避免内部逗号被切成多空
    ri = normalize_interval(ref)
    if ri is not None:
        si = normalize_interval(s)
        if si is None:
            return "manual"                 # 学生写的不是合法区间，不硬判错
        return "correct" if si == ri else "wrong"
    if normalize_interval(s) is not None:
        return "manual"                     # 学生写区间、标答不是区间
    refs = split_multi(ref)
    if len(refs) > 1:
        saw_manual = False
        for c in refs:
            v = _single_value_judge(s, c)
            if v == "correct":
                return "correct"
            if v == "manual":
                saw_manual = True
        return "manual" if saw_manual else "wrong"
    return _single_value_judge(s, ref)


def expand_answer_values(ref: str) -> List[str]:
    """把标答预展开成『可接受答案值』候选列表（M2-4）。

    只做**等价展开**，不新增标答未蕴含的值：

    - 多空题按空拆，多解题按 ``或`` / ``±`` 拆，各取原文；
    - 每个候选再补上去引导词形态（``x=-3/2`` -> ``-3/2``）与变量右侧值。

    因此 ``±2`` -> ``["2", "-2"]``，``x=-1 或 x=2`` -> ``["x=-1", "x=2", "-1", "2"]``。
    **漏解补全不在这里做**（那属教师判断）：教师在批改工单里确认「这种写法也算对」后
    才把该值追加进来，落库到 ``exam_paper_questions.answer_values``。
    """
    out: List[str] = []

    def _add(v: str) -> None:
        s = (v or "").strip()
        if s and s not in out:
            out.append(s)

    for part in split_blanks(ref):
        for c in split_multi(part):
            _add(c)
            _add(strip_lead(c))
            for v in _var_candidates(c):
                _add(v)
    return out


def blank_judge(
    student: str,
    ref: str,
    blanks: Optional[List[str]] = None,
    values: Optional[List[str]] = None,
) -> dict:
    """填空题三态判定（M2-3 + M2-4 答案值集合）。

    ``blanks``：学生逐空答案列表（对应 ``blank_index`` 顺序），有则优先于把
    ``student`` 单串按分隔符切分。多空题必须**逐空全对**才整题判对；任一空
    无法确定性判定则整题转人工（不猜）。

    ``values``：该题已确认的可接受答案值集合（``expand_answer_values`` 预展开 +
    教师批改时追加）。命中任一值即判对，**只救对不判错** —— 即便标答是
    ``sqrt(66)/6`` 这类原先要转人工的符号形态，教师确认过 ``1.354`` 后也能自动判。

    多空题**不使用** ``values``：扁平集合无法对应到具体某个空，拿整题集合去比
    会造成「空1 填了空2 的答案」也判对（静默误判，红线）。

    返回 ``{"status": "auto"|"manual", "correct": bool|None, "reason": str,
    "source": str}``。``source`` 是判据编码（M2-7 指标看板按它出分布），
    与面向人的 ``reason`` 分开维护。
    """
    if not (ref or "").strip():
        return _manual("标答为空，无法判分", "manual_no_answer")
    # 硬红线：系统给出的标答，学生照抄必须判对。无论标答结构多复杂（多解
    # 包裹、嵌套 LaTeX、区间并集），归一后完全一致即短路，绝不因解析不了
    # 而转人工或判错 —— 成绩错了不可逆，转人工只是多一道手续。
    s_all, r_all = norm_text(student or ""), norm_text(ref)
    if s_all and s_all == r_all:
        return _auto(True, "与标答一致", "blank_same")
    # 整题空着：明确判错（不是「判不了」）。单列一个判据来源，指标面板才能把
    # 「学生没写」和「机器解析不了」分开——后者才是指标下滑的信号。
    if not s_all and not any(str(x or "").strip() for x in (blanks or [])):
        return _auto(False, "未作答", "blank_empty")
    ref_parts = split_blanks(ref)
    if len(ref_parts) > 1:
        if blanks:
            stu_parts = [str(x) for x in blanks]
        else:
            stu_parts = split_blanks(student or "")
        if len(stu_parts) != len(ref_parts):
            if not any(x.strip() for x in stu_parts):
                return _auto(False, "未作答", "blank_empty")
            return _manual(
                f"这道题有 {len(ref_parts)} 个空，你填写了 {len(stu_parts)} 个，"
                f"机器无法自动比对（老师会看）",
                "manual_blanks",
            )
        verdicts = [_judge_single(a, b) for a, b in zip(stu_parts, ref_parts)]
    else:
        stu = str(blanks[0]) if blanks and len(blanks) == 1 else (student or "")
        # M2-4：教师已确认的答案值优先命中（单空题才用，见 docstring 红线）
        if values and stu.strip():
            for v in values:
                if (v or "").strip() and _judge_single(stu, v) == "correct":
                    return _auto(True, "命中已确认答案值", "blank_value")
        verdicts = [_judge_single(stu, ref_parts[0])]
    if any(v == "manual" for v in verdicts):
        return _manual(
            "这道题的答案含字母或符号表达式，机器判不了对错（老师会看）",
            "manual_symbol",
        )
    return _auto(all(v == "correct" for v in verdicts), source="blank_equiv")


def blank_equivalent(student: str, ref: str) -> bool:
    """兼容语义：是否**判对**。``manual`` 返回 False 交调用方兜底（agent LLM）。"""
    v = blank_judge(student, ref)
    return v["status"] == "auto" and v["correct"] is True


def blank_auto_gradeable(ref: str) -> bool:
    """该填空标答能否确定性判定（发布时统计人工量用，M2-3）。

    判据：每个空、每个多解候选都能解析成**数值**或**区间**规范形式。
    纯符号（``ln2``）、方程（``3x+4y-5=0``）、含变量表达式（``720-240(n+3)/2^n``）
    返回 False —— 这类答案学生答对时可字符串判对，答错时转人工。
    """
    if not (ref or "").strip():
        return False
    for part in split_blanks(ref):
        for c in split_multi(part):
            t = norm_text(c).strip()
            if not t:
                return False
            if normalize_interval(t) is not None:
                continue
            if _num_candidates(t):
                continue
            return False
    return True
