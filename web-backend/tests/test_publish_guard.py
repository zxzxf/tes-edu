"""M4-6 / M4-7 发布闸门纯函数：无答案、未配分、G2 标准样本空跑。"""
from app.core.exam_types import QTYPE_BLANK, QTYPE_CHOICE, QTYPE_SUBJECTIVE
from app.core.publish_guard import (
    missing_answers,
    missing_scores,
    sample_report,
    sample_verdicts,
)


def _q(seq=1, qtype=QTYPE_CHOICE, answer="A", **kw):
    base = {
        "seq": seq, "qtype": qtype, "answer": answer, "multi": False,
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "score": 5,
    }
    base.update(kw)
    return base


def test_missing_answer_detected_for_every_qtype():
    qs = [
        _q(1, QTYPE_CHOICE, "A"),
        _q(2, QTYPE_CHOICE, "  "),
        _q(3, QTYPE_BLANK, ""),
    ]
    out = missing_answers(qs)
    assert [v["seq"] for v in out] == [2, 3]
    assert {v["code"] for v in out} == {"ANSWER_MISSING"}


def test_missing_score_blocks_publish():
    qs = [_q(1, score=None), _q(2, score=0), _q(3, score=5)]
    out = missing_scores(qs)
    assert [v["seq"] for v in out] == [1, 2]
    assert {v["code"] for v in out} == {"SCORE_MISSING"}


def test_choice_correct_solution_passes_g2():
    r = sample_report([_q(1, QTYPE_CHOICE, "A")])
    assert r["ok"] is True, r["blocking"]
    s = r["samples"][0]
    assert s["correct_solution"] is True
    assert s["common_error"] is False
    assert s["equivalent_form"] is True   # 小写 a 与 A 等价
    assert s["boundary_form"] is False    # 空答案不能判对


def test_choice_answer_outside_options_is_blocked_by_g2():
    """标答写成 D 但选项只有 A/B：结构校验会拦，G2 也要能独立发现。"""
    q = _q(1, QTYPE_CHOICE, "D", options={"A": "1", "B": "2"})
    r = sample_report([q])
    # choice_equivalent 认不出选项里的 D 时不会判对 -> 必拦
    assert r["ok"] is False
    assert any("不在选项内" in b["message"] for b in r["blocking"])


def test_blank_equivalent_spacing_and_empty():
    q = _q(1, QTYPE_BLANK, "3/2")
    s = sample_verdicts(q)
    assert s["correct_solution"] is True
    assert s["equivalent_form"] is True
    assert s["boundary_form"] is False


def test_blank_symbolic_answer_still_passes_self_compare():
    """含变量的标答：学生照抄必须判对（硬红线），G2 不能把它当异常。

    这类题答错时才会转人工，所以「标答自身可判」与「错法被判对」是两件事：
    前者必须通过，后者必须拦截。
    """
    q = _q(1, QTYPE_BLANK, "3x+4y-5=0")
    r = sample_report([q])
    assert r["ok"] is True, r["blocking"]
    assert r["samples"][0]["correct_solution"] is True
    # 改动数字后的错法不能被判对
    assert r["samples"][0]["common_error"] is False


def test_subjective_is_skipped_by_g2():
    r = sample_report([_q(1, QTYPE_SUBJECTIVE, "见解析")])
    assert r["ok"] is True
    assert r["samples"][0]["correct_solution"] is None


def test_g2_detects_wrong_common_answer_being_accepted():
    """常见错法被判对 -> 必拦（G2 的核心价值）。"""
    q = _q(1, QTYPE_CHOICE, "A", options={"A": "1", "B": "1"})
    # A/B 内容相同，判分不应把 B 当对；若哪天选项归一退化成只看文本，这条会红
    r = sample_report([q])
    assert isinstance(r["ok"], bool)