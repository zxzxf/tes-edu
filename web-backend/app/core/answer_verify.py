"""答案双源交叉校验（M2-9 / G1）。

**要解决的问题**：作业闭环里 `exam_paper_questions.answer` 是判分的唯一来源。
它一旦是错的，整卷学生就会被系统性判错，而且错得「很稳定」——没有任何指标会报警
（机器每次都很自信地判错）。M2-10 的改判护栏是**事后**止损（已经判错 K 次才冻结），
G1 是**事前**把错误答案拦在发布之前。

四档校验依据（按可信度排序）
--------------------------
1. ``verified``：**两个独立来源一致**。当前唯一能构成独立第二来源的是
   「题库现行答案 ≠ 卷面快照」——说明组卷之后有人修过题库，此时两个值来自
   不同时点、不同写入方，比对才有验证力。若题库与快照完全相同（刚组完卷的常态），
   那是**同源**，不算验证（否则 G1 就是「自己等于自己」的空壳）。
2. ``mechanical_pass``：**机械代入检验**。题干里有单变量等式（``x^2-2x-3=0``、
   ``2^n=8``）、答案是数值 → 把答案代回等式看是否成立。这是唯一能自动证明
   「答案与题干自洽」的手段，不依赖任何外部来源。
3. ``teacher_confirmed``：无以上依据时，**教师确认**（人工裁决，可顺手修正答案）。
4. 其余一律 ``unverified`` / ``conflict`` → **不许发布**。

LLM 的位置：只给建议（``llm_suggestion``），**绝不作为通过依据**，也不自动改答案。
模型说「我算出来是 C」时，教师看到的是提示，不是判决——把 LLM 当第二来源等于
让两个都会出错的模型互相背书。

红线
----
- **不确定 ≠ 通过**：没有第二来源、机械也不适用 → ``unverified``，逼教师看一眼。
- **机械检验失败不报假冲突**：题干可能含参数（``已知 a=2, b=3, 求 a+b``），
  代入必然不成立。因此失败只标 ``suspect``（提示可疑），状态仍是待确认，
  避免用错误的理由拦住一道好题。
- 本模块纯函数，不碰 DB / 网络，题目既可传 ORM 对象也可传 dict。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.core.equiv import blank_judge, choice_equivalent, norm_text, to_number

# ---------------------------------------------------------------- 来源编码
SOURCE_PAPER = "paper"          # 卷面答案快照（primary，永远是它）
SOURCE_KB = "kb"                # 题库现行答案（与快照不同源时才构成第二来源）
SOURCE_TEACHER = "teacher"      # 教师人工裁决
SOURCE_MECHANICAL = "mechanical"  # 机械代入检验（不是独立来源，是替代验证）

SOURCE_LABEL: Dict[str, str] = {
    SOURCE_PAPER: "卷面答案",
    SOURCE_KB: "题库现行答案",
    SOURCE_TEACHER: "教师确认",
    SOURCE_MECHANICAL: "机械代入检验",
}

# ---------------------------------------------------------------- 状态编码
STATUS_VERIFIED = "verified"              # 双源一致
STATUS_MECHANICAL = "mechanical_pass"     # 代入检验通过
STATUS_TEACHER = "teacher_confirmed"      # 教师确认
STATUS_CONFLICT = "conflict"              # 双源冲突
STATUS_UNVERIFIED = "unverified"          # 无依据

STATUS_LABEL: Dict[str, str] = {
    STATUS_VERIFIED: "双源一致",
    STATUS_MECHANICAL: "代入检验通过",
    STATUS_TEACHER: "教师已确认",
    STATUS_CONFLICT: "双源冲突",
    STATUS_UNVERIFIED: "待确认",
}

#: 可以发布的三种状态。其余（conflict / unverified / 未知）一律拦截。
PASSING_STATUSES = frozenset({STATUS_VERIFIED, STATUS_MECHANICAL, STATUS_TEACHER})

#: 机械检验代码
MECH_OK = "subst_ok"
MECH_FAIL = "subst_fail"
MECH_NA = "subst_na"


def status_label(status: Optional[str]) -> str:
    return STATUS_LABEL.get(status or "", status or "待确认")


def is_passing(status: Optional[str]) -> bool:
    return (status or "") in PASSING_STATUSES


def needs_teacher(status: Optional[str]) -> bool:
    """该题是否必须教师出手（冲突 或 无依据）。"""
    return (status or "") in (STATUS_CONFLICT, STATUS_UNVERIFIED)


# ---------------------------------------------------------------- 答案等价
def answer_equivalent(
    qtype: str,
    a: str,
    b: str,
    multi: bool = False,
    options: Any = None,
) -> bool:
    """两个答案是否等价（选择题按标签集合，其余按 equiv 三态取「已判对」）。"""
    if qtype == "choice":
        opt_map = options if isinstance(options, dict) else None
        return choice_equivalent(a, b, multi=multi, options=opt_map) is True
    v = blank_judge(a or "", b or "")
    return v["status"] == "auto" and v["correct"] is True


# ---------------------------------------------------------------- 代入检验
# 归一化后的题干里找「左侧表达式 = 右侧常数」。norm_text 已把 ``^`` 转 ``**``、
# ``√`` 转 ``sqrt``、全角括号转半角，所以字符集按 ASCII 写即可。
_EQ_RE = re.compile(r"([0-9a-z\*\+\-/\(\)\.\s]{2,80}?)\s*=\s*(-?\d+(?:\.\d+)?)")

#: 求值白名单里的函数名 / 常量，识别变量时必须先剔掉（否则 ``sqrt`` 会被当成 s,q,r,t）
_FUNC_TOKENS = ("sqrt", "nrt", "ln", "log", "exp", "sin", "cos", "tan", "pi", "inf")
_LETTER = re.compile(r"[a-z]")


def _variables(lhs: str) -> List[str]:
    """左侧表达式里出现的「真变量」字母集合。

    先剔掉函数名与常量（``sqrt``/``pi``…），再要求**只剩一个字母**。
    ``f(x)`` 这类函数记号直接判为不可代入（那是函数不是方程的解）。
    """
    t = lhs
    for tok in _FUNC_TOKENS:
        t = t.replace(tok, " ")
    letters = sorted(set(_LETTER.findall(t)))
    if len(letters) != 1:
        return []
    var = letters[0]
    if re.search(rf"{var}\s*\(", lhs):     # f(x) / x( ... 不是「单变量等式」
        return []
    return [var]


def _holds(lhs: str, rhs: str, var: str, value: float) -> Optional[bool]:
    """把 ``var = value`` 代回等式；解析不了返回 None（不可判定 ≠ 不成立）。"""
    expr = lhs.replace(var, f"({value:g})")
    left = to_number(expr)
    right = to_number(rhs)
    if left is None or right is None:
        return None
    scale = max(1.0, abs(left), abs(right))
    return abs(left - right) <= 1e-6 * scale


def substitution_check(stem: str, answer: str) -> Dict[str, Any]:
    """机械代入检验：答案代回题干中的单变量等式是否成立。

    返回 ``{"applicable": bool, "passed": bool|None, "code": str, "detail": str}``。

    **适用范围刻意很窄**，只收「能真证明」的情形：

    - 题干含 ``左侧表达式 = 常数``，左侧只有一个变量，且左侧不是单个变量本身
      （``a=2`` 是参数赋值，不是待解的方程，代入必然不成立 → 跳过）；
    - 答案是**纯数值**（含分数/根式/π）。

    多个等式时任一成立即通过：题干里常有「已知 f(1)=2」之类的陪衬式，
    要求全部成立会把好题误判为可疑。
    """
    num = to_number(answer or "")
    if num is None:
        return {
            "applicable": False,
            "passed": None,
            "code": MECH_NA,
            "detail": "答案不是数值，无法代入检验（需第二来源或教师确认）",
        }
    text = norm_text(stem or "")
    tried = 0
    for m in _EQ_RE.finditer(text):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if not lhs or lhs in ("", ) or len(lhs) < 2:
            continue
        vs = _variables(lhs)
        if len(vs) != 1:
            continue
        ok = _holds(lhs, rhs, vs[0], num)
        if ok is None:
            continue
        tried += 1
        if ok:
            return {
                "applicable": True,
                "passed": True,
                "code": MECH_OK,
                "detail": f"把答案 {answer.strip()} 代入 {lhs}={rhs} 成立",
            }
    if tried:
        return {
            "applicable": True,
            "passed": False,
            "code": MECH_FAIL,
            "detail": (
                "把答案代入题干等式不成立（题干可能含参数，或答案本身有误），"
                "请教师核对后再发布"
            ),
        }
    return {
        "applicable": False,
        "passed": None,
        "code": MECH_NA,
        "detail": "题干没有可代入的单变量等式（需第二来源或教师确认）",
    }


def mechanical_check(
    qtype: str,
    answer: str,
    options: Any = None,
    stem: str = "",
    multi: bool = False,
) -> Dict[str, Any]:
    """机械可验证性：目前只有「代入检验」算真验证。

    选择题/解答题的结构合规性由 ``exam_types.validate_paper`` 管（那是发布门槛
    的另一半）；格式对不等于答案对，所以这里**不把格式检查当通过依据**。
    """
    if qtype != "blank":
        # 选择题的答案是一个标签，没有可代入的等式；解答题答案非数值。
        # 结构问题交给 validate_paper，这里只如实说「机械验证不适用」。
        return {
            "applicable": False,
            "passed": None,
            "code": MECH_NA,
            "detail": "该题型无法机械代入检验（需第二来源或教师确认）",
        }
    return substitution_check(stem, answer)


# ---------------------------------------------------------------- 单题判定
def evaluate(
    qtype: str,
    answer: str,
    multi: bool = False,
    options: Any = None,
    stem: str = "",
    secondary: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """给定卷面答案与（可选的）第二来源，得出该题校验结论。

    ``secondary``：``{"source": "kb", "answer": "..."}``。**只有真正独立的来源
    才应该传进来**；调用方负责判断「同源不算第二来源」。

    返回 ``{"status", "secondary_source", "secondary_answer", "suspect",
    "mechanical_code", "mechanical_detail", "detail"}``。
    """
    out: Dict[str, Any] = {
        "status": STATUS_UNVERIFIED,
        "secondary_source": None,
        "secondary_answer": None,
        "suspect": False,
        "mechanical_code": MECH_NA,
        "mechanical_detail": "",
        "detail": "",
    }

    if secondary and (secondary.get("answer") or "").strip():
        src = secondary.get("source") or SOURCE_KB
        other = secondary["answer"]
        out["secondary_source"] = src
        out["secondary_answer"] = other
        if answer_equivalent(qtype, answer, other, multi=multi, options=options):
            out["status"] = STATUS_VERIFIED
            out["detail"] = (
                f"两个独立来源一致（{SOURCE_LABEL.get(src, src)}与卷面答案等价）"
            )
        else:
            out["status"] = STATUS_CONFLICT
            out["suspect"] = True
            out["detail"] = (
                f"{SOURCE_LABEL.get(src, src)}为「{other}」，卷面答案为「{(answer or '').strip()}」，"
                "两者不一致：请教师裁决（大概率是其中一方被改过）"
            )
        return out

    mech = mechanical_check(qtype, answer, options=options, stem=stem, multi=multi)
    out["mechanical_code"] = mech["code"]
    out["mechanical_detail"] = mech["detail"]
    if mech["applicable"] and mech["passed"]:
        out["status"] = STATUS_MECHANICAL
        out["detail"] = mech["detail"]
    elif mech["applicable"] and not mech["passed"]:
        out["status"] = STATUS_UNVERIFIED
        out["suspect"] = True
        out["detail"] = mech["detail"]
    else:
        out["status"] = STATUS_UNVERIFIED
        out["detail"] = "无第二来源、无法机械验证：需教师确认后才可发布"
    return out


# ---------------------------------------------------------------- 汇总
def summarize(items: Sequence[Any]) -> Dict[str, Any]:
    """整卷校验汇总（接受 ORM 行或 dict）。"""
    counts: Dict[str, int] = {}
    for it in items:
        st = it.get("status") if isinstance(it, dict) else getattr(it, "status", None)
        st = st or STATUS_UNVERIFIED
        counts[st] = counts.get(st, 0) + 1
    total = sum(counts.values())
    passed = sum(counts.get(s, 0) for s in PASSING_STATUSES)
    return {
        "total": total,
        "passed": passed,
        "conflict": counts.get(STATUS_CONFLICT, 0),
        "unverified": counts.get(STATUS_UNVERIFIED, 0),
        "by_status": counts,
        "ok": total > 0 and passed == total,
    }


def gate_message(status: str, suspect: bool, detail: str) -> str:
    """发布拦截时给教师看的一句话（带操作指引，不只报「不能发」）。"""
    if status == STATUS_CONFLICT:
        return detail or "答案有两个不一致的来源，请裁决后再发布"
    if suspect:
        return detail or "答案可疑（代入检验未通过），请核对后再发布"
    return "答案无第二来源、也无法机械验证：请在答案校验里逐题确认后再发布"
