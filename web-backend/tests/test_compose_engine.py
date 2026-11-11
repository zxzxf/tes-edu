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


# ------------------------------------------------- 真实多标签分布（M4-2 生产验收回归）
def _realistic_pool(n=60):
    """复现生产题库分布：一题挂 3~6 个 kp、其中大量 kp 只出现在一道题里。

    2026-11-11 生产验收实测：69 道选择题 100% 带 2~4 个知识点、共 131 个 kp、
    63 个 kp 只有 1 道题。旧算法「按 kp 分配配额 + 逐 kp 抽题 + 全局 hash 去重」
    在这种分布下会让同一道题被多个配额 kp 争抢，题量稍大就误报「题源不足」，
    连「高三·数学·纯选择·10 题」的基线场景都出不了卷。
    """
    hot = [f"HOT-{i}" for i in range(8)]  # 少量被多题共享的 kp
    pool = []
    for i in range(n):
        solo = [f"SOLO-{i:03d}-{j}" for j in range(2 + i % 3)]      # 2~4 个独占 kp
        share = [hot[i % 8], hot[(i + 3) % 8]][:1 + i % 2]          # 1~2 个共享 kp
        pool.append(_q(f"h{i:03d}", solo + share, diff=1 + i % 5))
    return pool


def test_realistic_multilabel_no_kp_10_questions():
    """基线回归：不限 kp 出 10 题不再误报题源不足，且贪心确实在做最大覆盖。"""
    pool = _realistic_pool()
    quota = build_quota(pool, [], count=10)
    picked = pick_questions(pool, quota)
    assert len(picked) == 10
    hashes = [q["content_hash"] for q in picked]
    assert len(set(hashes)) == len(hashes)           # 同卷 content_hash 不重复
    covered = {k for q in picked for k in q["knowledge_points"]}
    # 10 道题至少覆盖 20 个不同 kp（平均每题带来 >=2 个新点），证明是最大覆盖而非随便抽
    assert len(covered) >= 2 * len(picked)


def test_realistic_multilabel_specified_kps_are_covered():
    """指定多个 kp 时先各保底覆盖、再整池补足到 count，不再报『需 N 实际 1』。"""
    pool = _realistic_pool()
    wanted = ["HOT-0", "HOT-1", "SOLO-000-0"]
    quota = build_quota(pool, wanted, count=10)
    picked = pick_questions(pool, quota)
    assert len(picked) == 10
    covered = {k for q in picked for k in q["knowledge_points"]}
    assert set(wanted).issubset(covered)
    assert len({q["content_hash"] for q in picked}) == 10


def test_realistic_pool_short_still_raises():
    """整池题目真的不够时仍显式报错，绝不静默凑卷（卡面第 4 条）。"""
    pool = _realistic_pool()
    quota = build_quota(pool, [], count=999)
    with pytest.raises(ComposeError) as ei:
        pick_questions(pool, quota)
    assert "题源不足" in str(ei.value)
    assert ei.value.shortfall


def test_realistic_unknown_kp_still_reported():
    """目标 kp 完全无题时点名报错，不静默少一个点。"""
    pool = _realistic_pool()
    with pytest.raises(ComposeError) as ei:
        pick_questions(pool, {"NO_SUCH_KP": 3})
    assert "NO_SUCH_KP" in str(ei.value)


def test_realistic_difficulty_mix_does_not_overdraw():
    """带难度配比的多标签场景也能抽满，且 hash 唯一（难度是软约束不阻断出卷）。"""
    pool = _realistic_pool()
    picked = pick_questions(pool, {"": 10}, difficulty_mix={1: 3, 3: 3, 5: 4})
    assert len(picked) == 10
    assert len({q["content_hash"] for q in picked}) == 10


def test_difficulty_mix_scarce_band_is_picked_first():
    """难度配比权重微调回归：稀缺难度档优先于「覆盖更多 kp」的多标签题。

    2026-11-11 M4-2 生产验收 E 场景（mix={1:5,3:5}）曾出现「难度 1 需 5、
    实际 0」：覆盖增益是主价值时，带 4 个 kp 的题每轮都压过只有 1 个 kp 的
    稀缺难度题，把后者全挤掉。仅把二值 d_gain 提到 gain 前也无效——多档同时
    欠题时所有题 d_gain 都是 1，第二项 gain 仍决定胜负。现在按缺口紧迫度排序，
    稀缺档必须先被抽满。
    """
    pool = (
        [_q(f"easy{i}", [f"EASY-{i}"], diff=1) for i in range(2)]
        + [_q(f"wide{i}", [f"W-{i}-{j}" for j in range(4)], diff=3) for i in range(6)]
    )
    picked = pick_questions(pool, {"": 4}, difficulty_mix={1: 2, 3: 2})
    dist = summarize_distribution(picked, difficulty_mix={1: 2, 3: 2})
    assert dist["by_difficulty"].get(1, 0) == 2      # 稀缺档被优先抽满
    assert dist["by_difficulty"].get(3, 0) == 2
    assert not dist["shortfall"]
    assert len({q["content_hash"] for q in picked}) == 4