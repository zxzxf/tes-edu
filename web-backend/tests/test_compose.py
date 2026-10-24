"""T-A4 组卷/变式接口单元测试（纯函数为主 + SQL 生成断言）。"""
from __future__ import annotations

import pytest

from app.services import compose


# ------------------------------------------------------------------ walk_down

def test_walk_down_single_hop():
    edges = [("B", "A"), ("C", "B")]
    assert compose.walk_down(edges, "A") == {"B", "C"}
    assert compose.walk_down(edges, "B") == {"C"}
    assert compose.walk_down(edges, "C") == set()


def test_walk_down_diamond_and_cycle_safe():
    # 菱形：A->B, A->C, B->D, C->D
    edges = [("B", "A"), ("C", "A"), ("D", "B"), ("D", "C")]
    assert compose.walk_down(edges, "A") == {"B", "C", "D"}
    # 坏数据环不死循环
    cyc = [("X", "Y"), ("Y", "X")]
    assert compose.walk_down(cyc, "X") == {"Y"}


# --------------------------------------------------------------- related_kps

def test_related_kps_tiers():
    edges = [("B", "A"), ("C", "B"), ("E", "A")]
    rel = compose.related_kps(edges, "A", max_tier=2)
    assert rel == {"A": 0, "B": 1, "C": 2, "E": 1}


def test_related_kps_max_tier_truncates():
    edges = [("B", "A"), ("C", "B"), ("D", "C")]
    rel = compose.related_kps(edges, "A", max_tier=1)
    assert rel == {"A": 0, "B": 1}


def test_related_kps_cycle_safe():
    rel = compose.related_kps([("X", "Y"), ("Y", "X")], "X", max_tier=2)
    assert rel["X"] == 0 and rel["Y"] == 1


# ----------------------------------------------------------- plan_composition

def test_plan_composition_covers_all_kps_with_min_one():
    related = {"A": 0, "B": 1, "C": 2}
    counts = {"A": 5, "B": 3, "C": 2}
    plan = compose.plan_composition(6, related, counts)
    # 保底轮每点 1 题，共 3；余量 3 按层权重补——tier0（A）优先吃满，再 tier1
    assert plan["A"] >= 1 and plan["B"] >= 1 and plan["C"] >= 1
    assert sum(plan.values()) == 6
    assert plan["A"] >= plan["B"] >= plan["C"]        # 低 tier 配额不低于高 tier


def test_plan_composition_prefers_low_tier_variants():
    """变式优先：tier0 题量充足时，余量先给同点变式（计划书「含变式」本意）。"""
    related = {"A": 0, "B": 1}
    counts = {"A": 10, "B": 2}
    plan = compose.plan_composition(5, related, counts)
    assert plan["A"] == 4 and plan["B"] == 1          # A 拿满余量，B 保底


def test_plan_composition_respects_counts_ceiling():
    related = {"A": 0, "B": 1}
    counts = {"A": 1, "B": 1}
    plan = compose.plan_composition(10, related, counts)
    # 题量封顶：总量不超过候选总数 2
    assert plan == {"A": 1, "B": 1}


def test_plan_composition_skips_zero_count_kps():
    related = {"A": 0, "B": 1}
    counts = {"A": 4, "B": 0}
    plan = compose.plan_composition(3, related, counts)
    assert "B" not in plan
    assert plan["A"] == 3


def test_plan_composition_empty_inputs():
    assert compose.plan_composition(0, {"A": 0}, {"A": 3}) == {}
    assert compose.plan_composition(5, {}, {}) == {}


# ----------------------------------------------------------- allocate_evenly

def test_allocate_evenly_balances_and_tops_up():
    counts = {"A": 5, "B": 2, "C": 1}
    plan = compose.allocate_evenly(6, counts)
    assert sum(plan.values()) == 6
    assert all(v >= 1 for v in plan.values())
    assert plan["A"] <= 5 and plan["B"] <= 2 and plan["C"] <= 1


def test_allocate_evenly_capped_by_counts():
    plan = compose.allocate_evenly(10, {"A": 2, "B": 1})
    assert plan == {"A": 2, "B": 1}


# ------------------------------------------------------------- pick_by_plan

def _q(h, kps, **kw):
    d = {"id": h, "content_hash": h, "knowledge_points": kps}
    d.update(kw)
    return d


def test_pick_by_plan_prefers_low_tier_and_dedupes():
    related = {"A": 0, "B": 1}
    plan = {"A": 1, "B": 1}
    pool = [_q("h1", ["A"]), _q("h2", ["A", "B"]), _q("h3", ["B"])]
    items = compose.pick_by_plan(pool, plan, related)
    hashes = [i["content_hash"] for i in items]
    assert len(hashes) == len(set(hashes))                     # 去重
    assert items[0]["kp_hit"] == "A" and items[0]["tier"] == 0
    assert {i["kp_hit"] for i in items} == {"A", "B"}


def test_pick_by_plan_respects_quota():
    related = {"A": 0}
    plan = {"A": 2}
    pool = [_q("h1", ["A"]), _q("h2", ["A"]), _q("h3", ["A"])]
    items = compose.pick_by_plan(pool, plan, related)
    assert len(items) == 2


def test_pick_by_plan_seen_excludes_prior():
    items = compose.pick_by_plan([_q("h1", ["A"])], {"A": 1}, {"A": 0}, seen={"h1"})
    assert items == []


# ------------------------------------------------------- SQL 生成（防回归）

def test_question_sql_uses_named_params_not_array_bind():
    sql = compose._question_select_sql(kp_count=2, type_count=1,
                                       need_difficulty=True, exclude_count=1)
    # 每个 KP 一个命名占位符（不用数组绑定 / 不用 ?|）
    assert "e = :kp0 OR e = :kp1" in sql
    assert "?|" not in sql and "CAST(" not in sql
    assert "difficulty BETWEEN :lo AND :hi" in sql
    assert "type IN (:t0)" in sql
    assert "content_hash NOT IN (:x0)" in sql
    assert "jsonb_array_elements_text(knowledge_points::jsonb)" in sql  # 列上 cast 安全
    # 不存在「绑定参数后接 ::」的坑形态
    assert ":::" not in sql and ":kp0:" not in sql


def test_question_sql_minimal_variant():
    sql = compose._question_select_sql(1, 0, False, 0)
    assert "difficulty BETWEEN" not in sql and "type IN" not in sql and "NOT IN" not in sql


# ------------------------------------------------------------ normalize_types

def test_normalize_types_cn_and_code():
    assert compose.normalize_types(["single", "单选", " solve "]) == ["单选", "解答"]
    assert compose.normalize_types(None) == []
    assert compose.normalize_types(["实验", "experiment"]) == ["实验"]
