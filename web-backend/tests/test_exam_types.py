"""M1-3 试卷题型枚举与发布校验（纯逻辑，不连库）。

锁定的契约：
- `paper_type` 三值：pure_choice / pure_blank / mixed
- 卷内题型白名单：纯选择卷含填空 → 拦；纯填空卷含选择 → 拦
- 混合卷含解答题 → **不拦**，计「需人工 N 道」
- 选择题答案不合规 / 选项不足 → 发布前就必须拦住
  （判分唯一依据是本卷答案快照，发布后才发现判不了就是错成绩）
"""
import pytest
from pydantic import ValidationError

from app.core.exam_types import (
    PAPER_TYPES,
    PAPER_TYPE_ALLOWED_QTYPES,
    PAPER_TYPE_MIXED,
    PAPER_TYPE_PURE_BLANK,
    PAPER_TYPE_PURE_CHOICE,
    QTYPE_BLANK,
    QTYPE_CHOICE,
    QTYPE_SUBJECTIVE,
    check_choice_answer,
    course_types_for,
    is_auto_gradeable,
    to_qtype,
    validate_paper,
)
from app.models.schemas import ExamPaperCreate


def _q(seq, qtype, answer, options=None, multi=False):
    if options is None and qtype == QTYPE_CHOICE:
        options = {"A": "1", "B": "2", "C": "3", "D": "4"}
    return {
        "seq": seq,
        "qtype": qtype,
        "multi": multi,
        "answer": answer,
        "options": options,
    }


# ---------------------------------------------------------------- 枚举映射
def test_paper_types_are_exactly_three():
    assert PAPER_TYPES == ("pure_choice", "pure_blank", "mixed")
    assert set(PAPER_TYPE_ALLOWED_QTYPES) == set(PAPER_TYPES)


def test_schema_rejects_unknown_paper_type():
    with pytest.raises(ValidationError):
        ExamPaperCreate(paper_type="pure_essay")
    # 三值均可构造
    for pt in PAPER_TYPES:
        assert ExamPaperCreate(paper_type=pt).paper_type == pt


def test_course_type_mapping():
    assert to_qtype("单选") == (QTYPE_CHOICE, False)
    assert to_qtype("多选") == (QTYPE_CHOICE, True)
    assert to_qtype("填空") == (QTYPE_BLANK, False)
    assert to_qtype("解答") == (QTYPE_SUBJECTIVE, False)
    assert to_qtype("多选题") == (QTYPE_CHOICE, True)
    assert to_qtype("不存在的题型") is None
    assert to_qtype(None) is None


def test_course_types_for_paper_type():
    pc = course_types_for(PAPER_TYPE_PURE_CHOICE)
    assert "单选" in pc and "多选" in pc
    assert "填空" not in pc and "解答" not in pc

    pb = course_types_for(PAPER_TYPE_PURE_BLANK)
    assert "填空" in pb and "单选" not in pb

    mx = course_types_for(PAPER_TYPE_MIXED)
    assert {"单选", "多选", "填空", "解答"} <= set(mx)


def test_course_types_for_unknown_paper_type_is_empty():
    assert course_types_for("nope") == []


# ---------------------------------------------------------------- 选择题答案
@pytest.mark.parametrize("answer,multi", [("C", False), ("c", False), ("ACD", True), (" C ", False)])
def test_choice_answer_ok(answer, multi):
    assert check_choice_answer(answer, multi) is None


@pytest.mark.parametrize(
    "answer,multi",
    [
        ("", False),          # 空答案
        (None, False),        # None
        ("BC", False),        # 单选多字母
        ("A", True),          # 多选仅 1 项
        ("E", False),         # 越界选项
        ("AA", True),         # 重复
        ("1", False),         # 非字母
    ],
)
def test_choice_answer_bad(answer, multi):
    assert check_choice_answer(answer, multi)


# ---------------------------------------------------------------- 整卷校验
def test_pure_choice_all_good():
    qs = [_q(1, QTYPE_CHOICE, "A"), _q(2, QTYPE_CHOICE, "ACD", multi=True)]
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is True
    assert r.question_count == 2
    assert r.auto_gradeable_count == 2
    assert r.manual_required_count == 0
    assert r.violations == []
    assert "纯自动判分" in r.note


def test_pure_choice_with_blank_is_blocked():
    qs = [_q(1, QTYPE_CHOICE, "A"), _q(2, QTYPE_BLANK, "2", options=None)]
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is False
    assert [v.code for v in r.violations] == ["QTYPE_NOT_ALLOWED"]
    v = r.violations[0]
    assert v.seq == 2 and v.qtype == QTYPE_BLANK
    assert "纯选择题卷" in v.message and "填空题" in v.message


def test_pure_blank_with_choice_is_blocked():
    qs = [_q(1, QTYPE_BLANK, "2"), _q(2, QTYPE_CHOICE, "A")]
    r = validate_paper(PAPER_TYPE_PURE_BLANK, qs)
    assert r.ok is False
    assert [v.code for v in r.violations] == ["QTYPE_NOT_ALLOWED"]
    assert r.violations[0].seq == 2


def test_pure_blank_counts_auto_and_manual_per_answer_shape():
    """M2-3：填空按**答案形态**逐题判定，不再按题型一刀切。

    ``1`` 可数值化 -> 自动判；``3x+4y-5=0`` 是方程，只能字符串判对、
    答错转人工（发布时就如实标出来，别等学生答完才发现判不了）。
    """
    qs = [_q(1, QTYPE_BLANK, "1"), _q(2, QTYPE_BLANK, "3x+4y-5=0")]
    r = validate_paper(PAPER_TYPE_PURE_BLANK, qs)
    assert r.ok is True
    assert r.auto_gradeable_count == 1
    assert r.manual_required_count == 1
    assert r.manual_breakdown == {QTYPE_BLANK: 1}
    assert "需人工批改 1 道" in r.note


@pytest.mark.parametrize(
    "answer,auto",
    [
        ("61/25", True),
        ("-3/2", True),
        ("[2,3)", True),
        ("(-∞,-4)∪(0,+∞)", True),
        (r"$\sqrt[3]{\dfrac32}$", True),
        ("ln2", True),                      # ln2 -> ln(2)，可数值化
        ("720 - 240(n+3)/2^n", False),      # 含变量 n，无法数值化
        ("3x+4y-5=0", False),               # 方程
        ("", False),                        # 缺答案
    ],
)
def test_is_auto_gradeable_blank_by_answer_shape(answer, auto):
    assert is_auto_gradeable(QTYPE_BLANK, answer) is auto


def test_is_auto_gradeable_other_qtypes():
    """选择题不看答案文本（答案合规由 check_choice_answer 单独拦）；解答题一律人工。"""
    assert is_auto_gradeable(QTYPE_CHOICE) is True
    assert is_auto_gradeable(QTYPE_CHOICE, "A") is True
    assert is_auto_gradeable(QTYPE_SUBJECTIVE, "见解析") is False


def test_mixed_with_subjective_is_allowed_but_marked():
    qs = [
        _q(1, QTYPE_CHOICE, "B"),
        _q(2, QTYPE_BLANK, "1"),
        _q(3, QTYPE_SUBJECTIVE, "见解析", options=None),
    ]
    r = validate_paper(PAPER_TYPE_MIXED, qs)
    assert r.ok is True
    assert r.qtype_counts == {QTYPE_CHOICE: 1, QTYPE_BLANK: 1, QTYPE_SUBJECTIVE: 1}
    # 选择题 + 可数值化的填空都自动判；解答题必人工
    assert r.auto_gradeable_count == 2
    assert r.manual_required_count == 1
    assert r.manual_breakdown == {QTYPE_SUBJECTIVE: 1}
    assert "解答题" in r.note and "填空题" not in r.note


def test_empty_paper_blocked():
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, [])
    assert r.ok is False
    assert [v.code for v in r.violations] == ["EMPTY_PAPER"]
    assert r.violations[0].seq == 0


def test_unknown_paper_type_blocked():
    r = validate_paper("pure_essay", [_q(1, QTYPE_CHOICE, "A")])
    assert r.ok is False
    assert [v.code for v in r.violations] == ["UNKNOWN_PAPER_TYPE"]


def test_none_paper_type_blocked():
    r = validate_paper(None, [_q(1, QTYPE_CHOICE, "A")])
    assert r.ok is False
    assert r.violations[0].code == "UNKNOWN_PAPER_TYPE"


def test_invalid_choice_answer_blocks_publish():
    qs = [_q(1, QTYPE_CHOICE, "BC")]  # 单选标成双字母
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is False
    assert [v.code for v in r.violations] == ["CHOICE_ANSWER_INVALID"]
    assert "单选题答案含多个选项" in r.violations[0].message


def test_multi_choice_single_letter_blocks_publish():
    qs = [_q(1, QTYPE_CHOICE, "A", multi=True)]
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is False
    assert r.violations[0].code == "CHOICE_ANSWER_INVALID"


def test_missing_options_blocks_publish():
    qs = [_q(1, QTYPE_CHOICE, "A", options={"A": "1"})]
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is False
    assert [v.code for v in r.violations] == ["CHOICE_OPTIONS_MISSING"]


def test_options_as_list_of_dicts_accepted():
    qs = [
        _q(
            1,
            QTYPE_CHOICE,
            "A",
            options=[{"label": "A", "content": "1"}, {"label": "B", "content": "2"}],
        )
    ]
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, qs)
    assert r.ok is True


def test_blank_without_answer_blocks_publish():
    qs = [_q(1, QTYPE_BLANK, "   ")]
    r = validate_paper(PAPER_TYPE_PURE_BLANK, qs)
    assert r.ok is False
    assert [v.code for v in r.violations] == ["BLANK_ANSWER_MISSING"]


def test_validate_accepts_orm_like_objects():
    class Row:
        def __init__(self, seq, qtype, answer, options, multi=False):
            self.seq, self.qtype, self.answer = seq, qtype, answer
            self.options, self.multi = options, multi

    r = validate_paper(
        PAPER_TYPE_PURE_CHOICE, [Row(1, QTYPE_CHOICE, "D", {"A": "1", "B": "2"})]
    )
    assert r.ok is True


def test_violations_as_dict_shape():
    r = validate_paper(PAPER_TYPE_PURE_CHOICE, [_q(3, QTYPE_SUBJECTIVE, "见解析", options=None)])
    d = r.as_dict()
    assert d["ok"] is False
    assert d["violations"][0] == {
        "seq": 3,
        "qtype": QTYPE_SUBJECTIVE,
        "code": "QTYPE_NOT_ALLOWED",
        "message": d["violations"][0]["message"],
    }
    assert set(d) == {
        "ok",
        "paper_type",
        "question_count",
        "qtype_counts",
        "auto_gradeable_count",
        "manual_required_count",
        "manual_breakdown",
        "violations",
        "note",
    }
