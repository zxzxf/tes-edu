"""知识点 DAG 验收纯函数测试（不连库，对齐 web-backend 测试哲学）。

覆盖 verify_dag.py 的四组校验：覆盖度 / 悬空边 / 无环 / 核心标记。
节点与边用普通 dict 表示，验证纯函数对 dict 输入的兼容性
（真实运行时输入是 asyncpg Record，行为一致）。
"""
import pytest

from scripts.verify_dag import (
    build_dag_report,
    check_acyclic,
    check_coverage,
    check_core,
    check_dangling,
)


def _node(code, title="T", subject="数学", importance="core", book="必修一"):
    return {
        "code": code,
        "title": title,
        "subject": subject,
        "importance": importance,
        "book": book,
    }


def _edge(kp, pre):
    return {"kp_code": kp, "prereq_code": pre, "relation_type": "prerequisite"}


# ---------------- 覆盖度 ---------------- #
def test_coverage_ok():
    nodes = [_node("A"), _node("B")]
    assert check_coverage(nodes)["ok"] is True


def test_coverage_missing_field():
    nodes = [{"code": "A", "title": "", "subject": "数学"}]  # title 空
    r = check_coverage(nodes)
    assert r["ok"] is False
    assert any("title" in m["reason"] for m in r["missing_fields"])


def test_coverage_duplicate_code():
    nodes = [_node("A"), _node("A")]
    r = check_coverage(nodes)
    assert r["ok"] is False
    assert "A" in r["duplicate_codes"]


# ---------------- 悬空边 ---------------- #
def test_dangling_ok():
    nodes = [_node("A"), _node("B")]
    edges = [_edge("B", "A")]
    assert check_dangling(nodes, edges)["ok"] is True


def test_dangling_kp_missing():
    nodes = [_node("A")]
    edges = [_edge("B", "A")]  # B 不存在
    r = check_dangling(nodes, edges)
    assert r["ok"] is False
    assert any("kp_code 不存在" in d["reason"] for d in r["dangling"])


def test_dangling_prereq_missing():
    nodes = [_node("A")]
    edges = [_edge("A", "Z")]  # Z 不存在
    r = check_dangling(nodes, edges)
    assert r["ok"] is False
    assert any("prereq_code 不存在" in d["reason"] for d in r["dangling"])


# ---------------- 无环 ---------------- #
def test_acyclic_ok_chain():
    # C <- B <- A （A 先学）
    nodes = [_node("A"), _node("B"), _node("C")]
    edges = [_edge("B", "A"), _edge("C", "B")]
    r = check_acyclic(nodes, edges)
    assert r["ok"] is True
    assert r["topo_covered"] == 3


def test_acyclic_detects_cycle():
    # A -> B -> A
    nodes = [_node("A"), _node("B")]
    edges = [_edge("B", "A"), _edge("A", "B")]
    r = check_acyclic(nodes, edges)
    assert r["ok"] is False
    assert set(r["cycle_nodes"]) == {"A", "B"}


def test_acyclic_self_loop():
    nodes = [_node("A")]
    edges = [_edge("A", "A")]
    r = check_acyclic(nodes, edges)
    assert r["ok"] is False


# ---------------- 核心标记 ---------------- #
def test_core_present():
    nodes = [_node("A", importance="core"), _node("B", importance="std")]
    assert check_core(nodes)["ok"] is True
    assert check_core(nodes)["core_count"] == 1


def test_core_absent():
    nodes = [_node("A", importance="std")]
    assert check_core(nodes)["ok"] is False


# ---------------- 聚合 ---------------- #
def test_build_dag_report_all_pass():
    nodes = [_node("A"), _node("B"), _node("C")]
    edges = [_edge("B", "A"), _edge("C", "B")]
    r = build_dag_report(nodes, edges)
    assert r["overall_ok"] is True
    assert r["summary"]["nodes"] == 3
    assert r["summary"]["edges"] == 2


def test_build_dag_report_fails_on_dangling():
    nodes = [_node("A")]
    edges = [_edge("A", "Z")]  # 悬空
    r = build_dag_report(nodes, edges)
    assert r["overall_ok"] is False
    assert r["checks"]["dangling"]["ok"] is False
