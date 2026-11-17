"""T-CAP4 入库自动校验管线单测（三闸 + quarantine 分流 + 写回语义）。

照 test_tcap2_variants.py 范式：直接 import 运维脚本模块，纯函数可测、无需 DB。
"""
import importlib.util
import os

import pytest

# scripts/intake_guard.py 以脚本方式加载（非 package）
_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "intake_guard.py",
)
_spec = importlib.util.spec_from_file_location("intake_guard", _SCRIPT)
gv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gv)

Q = gv.VERDICT_QUARANTINE
M = gv.VERDICT_MANUAL
P = gv.VERDICT_PASS


def row(stem="题干", type="填空", answer="1", content_hash="h", options=None, status="draft"):
    return {"stem": stem, "type": type, "answer": answer,
            "content_hash": content_hash, "options": options, "status": status}


def judge(r, secondary=None):
    return gv.judge_item(r, known_hashes=set(), known_fps=set(),
                         secondary_answer=secondary)


# ---------------------------------------------------------------- 闸1 去重
def test_dedup_exact_hash_quarantines():
    r = row()
    out = gv.judge_item(r, known_hashes={"h"}, known_fps=set())
    assert out["verdict"] == Q and out["gate"] == "dedup_exact"


def test_dedup_text_fingerprint_quarantines():
    r = row(stem="下列函数在(0,1)递增", type="选择", answer="B", options=["A.1", "B.2"])
    fp = gv.text_fingerprint(r["stem"])
    out = gv.judge_item(r, known_hashes=set(), known_fps={fp})
    assert out["verdict"] == Q and out["gate"] == "dedup_text"


def test_fingerprint_normalizes_format_but_keeps_numbers():
    a = gv.text_fingerprint("在区间(0,1)上 f(x) 递增")
    b = gv.text_fingerprint("在区间（0，1）上，f（x）递增")
    c = gv.text_fingerprint("在区间(0,2)上 f(x) 递增")
    assert a == b
    assert a != c


def test_no_dedup_when_hash_differs():
    out = gv.judge_item(row(), known_hashes={"other"}, known_fps={"other"})
    assert out["verdict"] != Q or out["gate"] != "dedup_exact"


# ---------------------------------------------------------------- 闸2 自洽
def test_missing_answer_quarantines():
    assert judge(row(answer=""))["verdict"] == Q
    assert judge(row(answer="   "))["gate"] == "missing_answer"


def test_choice_answer_out_of_range_quarantines():
    r = row(type="选择题", answer="H", options=["A.甲", "B.乙", "C.丙"])
    out = judge(r)
    assert out["verdict"] == Q and out["gate"] == "answer_out_of_range"


def test_choice_valid_answer_is_manual_not_quarantine():
    """红线：无第二来源的选择题只打标，不隔离（避免误伤正常题）。"""
    r = row(type="选择", answer="B", options=["A.甲", "B.乙"])
    out = judge(r)
    assert out["verdict"] == M and out["gate"] == "choice_no_second_source"


def test_mechanical_substitution_pass():
    r = row(stem="解方程 x^2-2x-3=0，求x", type="填空", answer="3")
    out = judge(r)
    assert out["verdict"] == P and out["gate"] == "mechanical_pass"


def test_mechanical_wrong_answer_is_manual_not_quarantine():
    """代入不通过转人工（可能是含参题），不硬隔离——G1 红线。"""
    r = row(stem="解方程 x^2-2x-3=0，求x", type="填空", answer="999")
    out = judge(r)
    assert out["verdict"] == M and out["gate"] == "mechanical_suspect"


def test_parameter_question_not_hard_quarantined():
    """含参题干代入必然不成立，不能当成错误答案隔离。"""
    r = row(stem="已知 a=2, b=3, 求 a+b", type="填空", answer="5")
    out = judge(r)
    assert out["verdict"] in (P, M)  # 绝不 quarantine


def test_non_numeric_subjective_is_manual():
    r = row(stem="证明：函数 f(x)=x 在 R 上递增", type="解答", answer="略")
    out = judge(r)
    assert out["verdict"] == M and out["gate"] in ("non_numeric", "numeric_no_reference")


def test_numeric_without_equation_is_manual():
    r = row(stem="求 1+1 的值", type="填空", answer="2")
    out = judge(r)
    assert out["verdict"] == M and out["gate"] == "numeric_no_reference"


# ---------------------------------------------------------------- 闸3 双源
def test_dual_source_consistent_passes():
    r = row(answer="3", type="填空")
    out = judge(r, secondary="3")
    assert out["verdict"] == P and out["gate"] == "dual_verified"


def test_dual_source_conflict_quarantines():
    r = row(answer="3", type="填空")
    out = judge(r, secondary="4")
    assert out["verdict"] == Q and out["gate"] == "dual_conflict"


def test_no_second_source_is_manual():
    r = row(answer="3", type="填空")
    out = judge(r)
    assert any(c["gate"] == "no_second_source" for c in out["checks"])


def test_homologous_source_not_false_verified():
    """同源（secondary 与答案相同）仍是本例的双源一致——调用方负责判同源不算，
    但本函数只如实比对；此处锁定「一致=pass」不误判为 conflict。"""
    r = row(answer="B", type="选择", options=["A.1", "B.2"])
    out = judge(r, secondary="B")
    assert out["verdict"] == P


# ---------------------------------------------------------------- 优先级与写回
def test_quarantine_beats_pass():
    """任一闸明确错误即隔离，即使另一闸通过。"""
    r = row(answer="3", type="填空", stem="解方程 x^2-2x-3=0，求x")
    out = gv.judge_item(r, known_hashes={"h"}, known_fps=set(), secondary_answer="3")
    assert out["verdict"] == Q  # dedup 命中压过 mechanical/dual pass


def test_pass_beats_manual():
    r = row(answer="3", type="填空")
    out = judge(r, secondary="3")
    assert out["verdict"] == P  # dual pass 压过 no_second_source 之外的 manual


def test_verdict_to_db_mapping():
    assert gv.verdict_to_db(Q) == {"status": "quarantine", "source_tag": "guard_quarantine"}
    assert gv.verdict_to_db(M, "draft") == {"status": "draft", "source_tag": "guard_manual_review"}
    assert gv.verdict_to_db(P, "draft") == {"status": "draft", "source_tag": "unchanged"}


def test_manual_does_not_regress_published():
    """已 published 的题即使判 manual，也不回退状态（保护组卷池）。"""
    out = gv.verdict_to_db(M, "published")
    assert out["status"] == "published" and "untouched" in out["source_tag"]


def test_all_verdicts_covered():
    """裁定集合封闭：只有 pass/manual/quarantine 三种。"""
    assert {P, M, Q} == {"pass", "manual_review", "quarantine"}
