"""试卷题型枚举与发布校验（M1-3）。

三值 ``paper_type``：

===============  ==============================================================
``pure_choice``  纯选择题卷：卷内只允许 ``choice``
``pure_blank``   纯填空题卷：卷内只允许 ``blank``
``mixed``        混合卷：``choice`` / ``blank`` / ``subjective`` 均可，解答题计「需人工 N 道」
===============  ==============================================================

设计依据（ADR-A10）：判分唯一来源是本卷答案快照 ``exam_paper_questions.answer``，
不调 LLM、不查外部检索。因此**选择题答案不合规必须在发布时拦住**——发布后才发现
判不了，学生已作答、成绩已错，且答案随卷快照不会随题库修复而更新。

反之，解答题**不拦**，只在发布结果上标记人工量，由 M3 人工工单消费。

本模块为纯函数（不碰 DB / ORM / 网络），题目既可传 ORM 对象也可传 dict，
便于单测直接构造用例。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.core.equiv import blank_auto_gradeable

# ---------------------------------------------------------------- 题型枚举
QTYPE_CHOICE = "choice"
QTYPE_BLANK = "blank"
QTYPE_SUBJECTIVE = "subjective"

QTYPE_LABEL: Dict[str, str] = {
    QTYPE_CHOICE: "选择题",
    QTYPE_BLANK: "填空题",
    QTYPE_SUBJECTIVE: "解答题",
}

PAPER_TYPE_PURE_CHOICE = "pure_choice"
PAPER_TYPE_PURE_BLANK = "pure_blank"
PAPER_TYPE_MIXED = "mixed"

PAPER_TYPES: Tuple[str, ...] = (
    PAPER_TYPE_PURE_CHOICE,
    PAPER_TYPE_PURE_BLANK,
    PAPER_TYPE_MIXED,
)

PAPER_TYPE_LABEL: Dict[str, str] = {
    PAPER_TYPE_PURE_CHOICE: "纯选择题卷",
    PAPER_TYPE_PURE_BLANK: "纯填空题卷",
    PAPER_TYPE_MIXED: "混合卷",
}

#: 卷型 -> 卷内允许的 qtype 白名单
PAPER_TYPE_ALLOWED_QTYPES: Dict[str, Set[str]] = {
    PAPER_TYPE_PURE_CHOICE: {QTYPE_CHOICE},
    PAPER_TYPE_PURE_BLANK: {QTYPE_BLANK},
    PAPER_TYPE_MIXED: {QTYPE_CHOICE, QTYPE_BLANK, QTYPE_SUBJECTIVE},
}

#: 题型级默认：这些题型**只要答案合规**就能确定性判分。
#: 填空（M2-3）不在此列 —— 它取决于**每道题的答案形态**：纯数值/区间/根式可自动判
#: （``61/25`` / ``[2,3)`` / ``\\sqrt[3]{3/2}``），含变量或符号函数的
#: （``720-240(n+3)/2^n`` / ``ln2`` / ``3x+4y-5=0``）只能字符串判对、答错转人工。
#: 因此填空题必须逐题过 :func:`is_auto_gradeable` 动态判定。
AUTO_GRADABLE_QTYPES: Set[str] = {QTYPE_CHOICE}

#: 题库中文题型 -> (qtype, multi)。兼容带/不带「题」字的写法。
COURSE_TYPE_TO_QTYPE: Dict[str, Tuple[str, bool]] = {
    "单选": (QTYPE_CHOICE, False),
    "单选题": (QTYPE_CHOICE, False),
    "选择": (QTYPE_CHOICE, False),
    "选择题": (QTYPE_CHOICE, False),
    "多选": (QTYPE_CHOICE, True),
    "多选题": (QTYPE_CHOICE, True),
    "填空": (QTYPE_BLANK, False),
    "填空题": (QTYPE_BLANK, False),
    "解答": (QTYPE_SUBJECTIVE, False),
    "解答题": (QTYPE_SUBJECTIVE, False),
    "计算": (QTYPE_SUBJECTIVE, False),
    "计算题": (QTYPE_SUBJECTIVE, False),
}

_CHOICE_ANSWER_RE = re.compile(r"^[A-Da-d]+$")
MAX_OPTIONS = 26


# ---------------------------------------------------------------- 基础映射
def is_known_paper_type(paper_type: Optional[str]) -> bool:
    return (paper_type or "") in PAPER_TYPE_ALLOWED_QTYPES


def to_qtype(course_type: Optional[str]) -> Optional[Tuple[str, bool]]:
    """题库 ``course_kb.type`` -> ``(qtype, multi)``；未知题型返回 None。"""
    return COURSE_TYPE_TO_QTYPE.get((course_type or "").strip())


def allowed_qtypes(paper_type: str) -> Optional[Set[str]]:
    return PAPER_TYPE_ALLOWED_QTYPES.get(paper_type)


def course_types_for(paper_type: str) -> List[str]:
    """反向映射：本卷型可以抽哪些 ``course_kb.type``（供 SQL ``IN`` 过滤）。"""
    allowed = PAPER_TYPE_ALLOWED_QTYPES.get(paper_type)
    if not allowed:
        return []
    return [ct for ct, (qt, _multi) in COURSE_TYPE_TO_QTYPE.items() if qt in allowed]


def is_auto_gradeable(qtype: str, answer: Optional[str] = None) -> bool:
    """该题能否确定性判分。

    **发布统计与提交判分共用此函数**，避免「发布时说自动判、提交时却转人工」的分叉。

    - 选择题：纳入 ``AUTO_GRADABLE_QTYPES`` 即自动判（答案合规另由
      :func:`check_choice_answer` 在发布时拦截）。
    - 填空题（M2-3）：看**这道题的答案形态**，纯数值/区间/根式可自动判，
      含变量或符号函数则只能字符串判对、答错转人工。
    - 解答题：一律人工。
    """
    if qtype in AUTO_GRADABLE_QTYPES:
        return True
    if qtype == QTYPE_BLANK:
        return blank_auto_gradeable(answer or "")
    return False


# ---------------------------------------------------------------- 单题校验
def check_choice_answer(answer: Optional[str], multi: bool) -> Optional[str]:
    """选择题答案合规性；合规返回 None，否则返回中文原因。"""
    a = (answer or "").strip()
    if not a:
        return "缺少答案（判分唯一依据缺失）"
    if not _CHOICE_ANSWER_RE.match(a):
        return f"答案格式非法 {a!r}，应为 A-D 字母组合"
    labels = {c.upper() for c in a}
    if len(labels) != len(a):
        return f"答案 {a!r} 含重复选项"
    if multi and len(labels) < 2:
        return f"多选题答案仅 1 个选项（{a}），至少需 2 个"
    if not multi and len(labels) > 1:
        return f"单选题答案含多个选项（{a}）"
    return None


def _option_count(options: Any) -> int:
    """统计选项个数（``{label: content}`` / ``[{label, content}]`` / ``[str]`` 均可）。"""
    if isinstance(options, dict):
        return len([k for k in options.keys() if str(k).strip()])
    if isinstance(options, (list, tuple)):
        n = 0
        for item in options:
            if isinstance(item, dict):
                if str(item.get("label") or item.get("key") or "").strip():
                    n += 1
            elif str(item).strip():
                n += 1
        return n
    return 0


def _get(q: Any, name: str, default: Any = None) -> Any:
    """同时支持 ORM 对象与 dict。"""
    if isinstance(q, dict):
        return q.get(name, default)
    return getattr(q, name, default)


# ---------------------------------------------------------------- 结果结构
@dataclass
class Violation:
    """一条拦截原因。``seq=0`` 表示整卷级问题。"""

    seq: int
    qtype: str
    code: str
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {"seq": self.seq, "qtype": self.qtype, "code": self.code, "message": self.message}


@dataclass
class ValidationReport:
    ok: bool = False
    paper_type: str = ""
    question_count: int = 0
    qtype_counts: Dict[str, int] = field(default_factory=dict)
    auto_gradeable_count: int = 0
    manual_required_count: int = 0
    manual_breakdown: Dict[str, int] = field(default_factory=dict)
    violations: List[Violation] = field(default_factory=list)

    @property
    def note(self) -> str:
        """给教师看的一句话结论。"""
        if not self.ok:
            return f"不可发布：{len(self.violations)} 处问题待修复"
        if not self.manual_required_count:
            return "纯自动判分卷，全部题目可确定性判分"
        parts = [
            f"{self.manual_breakdown.get(t, 0)} 道{QTYPE_LABEL.get(t, t)}"
            for t in (QTYPE_SUBJECTIVE, QTYPE_BLANK)
            if self.manual_breakdown.get(t)
        ]
        return f"需人工批改 {self.manual_required_count} 道（{'、'.join(parts)}）"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "paper_type": self.paper_type,
            "question_count": self.question_count,
            "qtype_counts": dict(self.qtype_counts),
            "auto_gradeable_count": self.auto_gradeable_count,
            "manual_required_count": self.manual_required_count,
            "manual_breakdown": dict(self.manual_breakdown),
            "violations": [v.as_dict() for v in self.violations],
            "note": self.note,
        }


# ---------------------------------------------------------------- 整卷校验
def validate_paper(paper_type: Optional[str], questions: Iterable[Any]) -> ValidationReport:
    """发布前整卷校验。

    拦截（``ok=False``）：未知卷型 / 空卷 / 题型越出白名单 / 选择题答案不合规 /
    选择题选项不足 / 填空题缺答案。
    不拦只标：解答题一律计入人工；填空题**按答案形态逐题判定** —— 纯数值/区间/
    根式计入 ``auto_gradeable_count``，含变量或符号函数的计入 ``manual_required_count``
    （这类题答对可字符串判对、答错转人工，见 M2-3）。
    """
    qs = list(questions)
    report = ValidationReport(paper_type=paper_type or "", question_count=len(qs))

    if not is_known_paper_type(paper_type):
        report.violations.append(
            Violation(
                seq=0,
                qtype="",
                code="UNKNOWN_PAPER_TYPE",
                message=f"未知卷型 {paper_type!r}，允许值：{', '.join(PAPER_TYPES)}",
            )
        )
        return report

    if not qs:
        report.violations.append(
            Violation(seq=0, qtype="", code="EMPTY_PAPER", message="卷内没有题目，无法发布")
        )
        return report

    allowed = PAPER_TYPE_ALLOWED_QTYPES[paper_type]
    label = PAPER_TYPE_LABEL[paper_type]

    for q in qs:
        seq = int(_get(q, "seq", 0) or 0)
        qtype = str(_get(q, "qtype", "") or "").strip()
        multi = bool(_get(q, "multi", False))
        answer = _get(q, "answer", "")

        report.qtype_counts[qtype] = report.qtype_counts.get(qtype, 0) + 1
        # 逐题统计自动判分能力：blank 取决于答案形态，不能按题型一刀切
        if is_auto_gradeable(qtype, answer):
            report.auto_gradeable_count += 1
        elif qtype in QTYPE_LABEL:
            report.manual_breakdown[qtype] = report.manual_breakdown.get(qtype, 0) + 1

        if qtype not in allowed:
            report.violations.append(
                Violation(
                    seq=seq,
                    qtype=qtype,
                    code="QTYPE_NOT_ALLOWED",
                    message=f"第 {seq} 题为「{QTYPE_LABEL.get(qtype, qtype or '未知题型')}」，"
                    f"{label}不允许该题型（允许："
                    f"{'、'.join(QTYPE_LABEL[t] for t in sorted(allowed))}）",
                )
            )
            continue  # 题型都不对，不再追加答案类噪音

        if qtype == QTYPE_CHOICE:
            reason = check_choice_answer(answer, multi)
            if reason:
                report.violations.append(
                    Violation(seq, qtype, "CHOICE_ANSWER_INVALID", f"第 {seq} 题：{reason}")
                )
            n_opt = _option_count(_get(q, "options", None))
            if n_opt < 2:
                report.violations.append(
                    Violation(
                        seq,
                        qtype,
                        "CHOICE_OPTIONS_MISSING",
                        f"第 {seq} 题：选项少于 2 个（实际 {n_opt} 个），学生无法作答",
                    )
                )
        elif qtype == QTYPE_BLANK:
            if not str(answer or "").strip():
                report.violations.append(
                    Violation(
                        seq,
                        qtype,
                        "BLANK_ANSWER_MISSING",
                        f"第 {seq} 题：填空题缺少答案，人工批改无参照",
                    )
                )

    report.manual_required_count = sum(report.manual_breakdown.values())
    report.ok = not report.violations
    return report
