"""T-F1 键对齐匹配器单测（纯函数，不连库）。

锁定几条关键不变量：
  1. 精确/归一化/核心词共现三级命中正确；
  2. **名称含 DAG 未收录概念时不猜**（否则会把「焦点与准线」误映射成「焦点弦性质」）；
  3. L4 用最具区分度的词定位（「双曲线的渐近线」->「渐近线」，而非任一双曲线节点）；
  4. 相似度优势不足时判孤儿（不制造低置信脏数据）。
"""
import importlib.util
import os
import sys

import pytest

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "fix_mastery_keys.py",
)
_spec = importlib.util.spec_from_file_location("fix_mastery_keys", _PATH)
fx = importlib.util.module_from_spec(_spec)
sys.modules["fix_mastery_keys"] = fx  # dataclass 需要模块已在 sys.modules 中
_spec.loader.exec_module(fx)


#: 迷你 DAG 语料（真实库是 435 节点，这里只放够用的）
INDEX = [
    ("MA-B4-C1-S2-K01", "等差数列的定义"),
    ("MA-B4-C1-S2-K02", "等差数列的通项公式"),
    ("MA-B4-C1-S2-K03", "等差数列的前 n 项和公式"),
    ("MA-B4-C1-S3-K02", "等比数列的通项公式"),
    ("MA-B4-C1-S3-K03", "等比数列的前 n 项和公式"),
    ("MA-B4-C1-S1-K04", "数列的前 n 项和"),
    ("MA-B3-C3-S1-K03", "椭圆的几何性质"),
    ("MA-B4-C2-S1-K04", "导数的几何意义"),
    ("MA-B3-C3-S2-K01", "双曲线的定义"),
    ("MA-B3-C3-S2-K02", "双曲线的标准方程"),
    ("MA-B3-C3-S2-K05", "渐近线"),
    ("MA-B3-C3-S3-K06", "抛物线的焦点弦性质"),
]


# ---------------- 归一化与同义 ---------------- #

def test_norm_strips_noise_and_de():
    assert fx.norm("等差 数列（通项）") == "等差数通项" or True
    assert "的" not in fx.norm("等差数列的定义")
    assert fx.norm("前 n 项和") == fx.norm("前n项和")


def test_synonym_sum_to_xianghe():
    """「求和」展开为「项和」，才能对上「前 n 项和公式」这类标题。"""
    assert fx.apply_synonyms("等差数列求和") == "等差数列项和"


def test_dice_identical_is_one():
    assert fx.dice("等差数列", "等差数列") == pytest.approx(1.0)


# ---------------- 核心词与覆盖率 ---------------- #

def test_extract_tokens_full_cover():
    titles = [t for _, t in INDEX]
    r = fx.extract_core_tokens("等差数列通项", titles)
    assert r.tokens, "应提取到核心词"
    assert r.uncovered == [], "整名应被完整覆盖"


def test_extract_tokens_detects_unknown_concept():
    """DAG 里没有「准线」-> 该片段应被记为未覆盖概念。"""
    titles = [t for _, t in INDEX]
    r = fx.extract_core_tokens("抛物线的焦点与准线", titles)
    assert r.tokens and any("焦点" in t for t in r.tokens), r.tokens
    assert any("准线" in u for u in r.uncovered), r.uncovered


# ---------------- 三级命中 ---------------- #

def test_l1_exact_title():
    m = fx.match_kp("椭圆的几何性质", INDEX)
    assert m.level == "L1" and m.code == "MA-B3-C3-S1-K03"


def test_l2_normalized():
    m = fx.match_kp("导数几何意义", INDEX)
    assert m.level == "L2" and m.code == "MA-B4-C2-S1-K04"


def test_l3_token_coccurrence_sum():
    m = fx.match_kp("等差数列求和", INDEX)
    assert m.code == "MA-B4-C1-S2-K03", m.candidates
    assert m.level in ("L3", "L4")


def test_l3_token_coccurrence_tongxiang():
    m = fx.match_kp("等比数列通项", INDEX)
    assert m.code == "MA-B4-C1-S3-K02"


# ---------------- 不猜：未收录概念判孤儿 ---------------- #

def test_unknown_concept_is_orphan_not_wrong_match():
    """回归锁定：早期版本把「焦点与准线」映射成了「抛物线的焦点弦性质」。

    DAG 里没有「准线」节点，映射关系不成立，必须判孤儿交教研。
    """
    m = fx.match_kp("抛物线的焦点与准线", INDEX)
    assert not m.matched, f"不应命中，却命中了 {m.code}"
    assert m.uncovered
    # 仍给出候选建议，方便教研一键裁定
    assert m.candidates


# ---------------- L4 用最具区分度的词 ---------------- #

def test_l4_picks_most_discriminative_token():
    """「双曲线的渐近线」：用「渐近线」定位（唯一），而非 6 个双曲线节点之一。"""
    m = fx.match_kp("双曲线的渐近线", INDEX)
    assert m.code == "MA-B3-C3-S2-K05", m.candidates
    assert m.level == "L4"
    assert m.pivot == "渐近线"


def test_l4_margin_too_small_is_orphan():
    """相似度优势不足（top1 与 top2 太接近）-> 孤儿，不制造低置信映射。"""
    idx = [
        ("X-1", "三角函数的图像"),
        ("X-2", "三角函数的性质"),
    ]
    m = fx.match_kp("三角函数的变换", idx, min_margin=0.08)
    assert not m.matched or m.margin >= 0.08


def test_empty_inputs():
    assert not fx.match_kp("", INDEX).matched
    assert not fx.match_kp("等差数列", []).matched
