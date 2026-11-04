"""M4 组卷引擎：配额 / 难度配比 / 知识点覆盖 / 题源不足诊断。

M4-2 的五条硬要求（卡面）逐条落在这里：
1. 只抽 published + 非 quarantine + 有答案的题；
2. 同卷 content_hash 不重复；
3. 知识点覆盖优先，其次题量/难度分布，同类题优先教师题；
4. 题源不足必须**显式报错并指明缺哪个维度**，不许静默凑卷；
5. 难度比例可配置，抽不满时回报实际分布。

纯函数层不连库，DB 查询在服务薄层（``compose_exam``），便于直接构造候选用例。
"""
import pytest

from app.core.compose_engine import (
    ComposeError,
    allocate_by_kp,
    build_quota,
    pick_questions,
    summarize_distribution,
)
from app.core.exam_types import QTYPE_CHOICE


def _q(h, kps, diff=3, source="import", qtype=QTYPE_CHOICE, answer="A"):
    return {
        "content_hash": h,
        "knowledge_points": list(kps),
        "difficulty": diff,
        "source_tag": source,
        "qtype": qtype,
        "answer": answer,
    }


# ---------------------------------------------------------------- 知识点覆盖
def test_each_kp_gets_at_least_one_when_possible():
    """知识点覆盖优先：有题的每个目标点先各保底 1 题。"""
    pool = [
        _q("a", ["KP1"]), _q("b", ["KP1"]), _q("c", ["KP1"]),
        _q("d", ["KP2"]), _q("e", ["KP3"]),
    ]
    quota = build_quota(pool, ["KP1", "KP2", "KP3"], count=5)
    assert quota["KP1"] >= 1 and quota["KP2"] >= 1 and quota["KP3"] >= 1
    assert sum(quota.values()) == 5


def test_kp_without_questions_is_reported_not_silently_dropped():
    """某个目标知识点一题都没有时必须报错并点名，不能悄悄少一个点。"""
    pool = [_q("a", ["KP1"]), _q("b", ["KP1"])]
    with pytest.raises(ComposeError) as ei:
        build_quota(pool, ["KP1", "KP_MISSING"], count=4)
    assert "KP_MISSING" in str(ei.value)


def test_no_kp_requirement_means_plain_count():
    pool = [_q(str(i), ["KP1"]) for i in range(10)]
    quota = build_quota(pool, [], count=4)
    assert sum(quota.values()) == 4


# ---------------------------------------------------------------- 去重 / 过滤
def test_same_content_hash_never_picked_twice():
    pool = [_q("dup", ["KP1"]), _q("dup", ["KP1"]), _q("x", ["KP1"])]
    picked = pick_questions(pool, {"KP1": 2})
    hashes = [q["content_hash"] for q in picked]
    assert hashes.count("dup") == 1
    assert len(set(hashes)) == len(hashes)


def test_teacher_questions_preferred_within_same_kp():
    """同类题优先教师题：题量够时，教师题应排在 import 之前被抽中。"""
    pool = [
        _q("i1", ["KP1"], source="import"),
        _q("t1", ["KP1"], source="teacher"),
        _q("i2", ["KP1"], source="import"),
    ]
    picked = pick_questions(pool, {"KP1": 1})
    assert [q["content_hash"] for q in picked] == ["t1"]


def test_pick_raises_when_pool_short_with_dimension_detail():
    """题源不足要指明缺哪个维度，而不是只报一句"不够"。"""
    pool = [_q("a", ["KP1"], diff=5)]
    with pytest.raises(ComposeError) as ei:
        pick_questions(pool, {"KP1": 3}, difficulty_mix={1: 2, 5: 1})
    msg = str(ei.value)
    assert "KP1" in msg or "难度" in msg


# ---------------------------------------------------------------- 难度配比
def test_difficulty_mix_is_respected_when_enough():
    pool = (
        [_q(f"e{i}", ["KP1"], diff=1) for i in range(5)]
        + [_q(f"m{i}", ["KP1"], diff=3) for i in range(5)]
    )
    picked = pick_questions(pool, {"KP1": 4}, difficulty_mix={1: 2, 3: 2})
    dist = summarize_distribution(picked)
    assert dist["by_difficulty"][1] == 2
    assert dist["by_difficulty"][3] == 2


def test_short_difficulty_falls_back_and_reports_actual():
    """某档难度不够时：能抽多少抽多少，并回报实际分布（卡面第 5 条）。"""
    pool = (
        [_q("e1", ["KP1"], diff=1)]
        + [_q(f"m{i}", ["KP1"], diff=3) for i in range(5)]
    )
    picked = pick_questions(pool, {"KP1": 4}, difficulty_mix={1: 3, 3: 1})
    dist = summarize_distribution(picked, difficulty_mix={1: 3, 3: 1})
    assert dist["by_difficulty"][1] == 1
    assert dist["shortfall"]
    assert "难度" in dist["shortfall"][0]


def test_allocate_by_kp_sums_to_count():
    counts = {"KP1": 5, "KP2": 5, "KP3": 5}
    quota = allocate_by_kp(counts, 7)
    assert sum(quota.values()) == 7


def test_allocate_by_kp_caps_at_availability():
    counts = {"KP1": 1, "KP2": 5}
    quota = allocate_by_kp(counts, 4)
    assert quota["KP1"] == 1          # 不能超过该点可用量
    assert sum(quota.values()) == 4

    # 要的比总可用量还多时，只能给到可用上限
    quota = allocate_by_kp({"KP1": 1, "KP2": 1}, 9)
    assert sum(quota.values()) == 2