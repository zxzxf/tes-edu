"""T4 S6 评测脚手架单元测试（eval_metrics 纯函数 + 脚本全流程 mock 自测）。"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import eval_metrics  # noqa: E402


# ------------------------------------------------------------------ 金标准解析

def _write_csv(path: Path, rows: list[dict]) -> str:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(eval_metrics.GOLD_FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return str(path)


def _row(**kw) -> dict:
    base = {c: "" for c in eval_metrics.GOLD_FIELDS}
    base.update(kw)
    return base


def test_load_gold_csv_happy_path(tmp_path):
    p = _write_csv(tmp_path / "g.csv", [
        _row(content_hash="h1", 题型="解答", 学生作答过程="很长的学生作答过程文本超过十二个字",
             金标准首错步骤="2", 金标准错因="calc", 评分人A="calc", 评分人B="calc"),
    ])
    rows = eval_metrics.load_gold_csv(p)
    assert len(rows) == 1
    assert rows[0]["金标准首错步骤"] == "2" and rows[0]["题型"] == "解答"


def test_load_gold_csv_missing_column(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("content_hash,学生作答过程\nh1,作答\n", encoding="utf-8")
    with pytest.raises(eval_metrics.GoldFormatError, match="缺列"):
        eval_metrics.load_gold_csv(str(p))


def test_load_gold_csv_missing_required(tmp_path):
    p = _write_csv(tmp_path / "g.csv", [_row(题型="解答", 学生作答过程="有过程但没 hash")])
    with pytest.raises(eval_metrics.GoldFormatError, match="缺必填值"):
        eval_metrics.load_gold_csv(p)


def test_to_int_and_norm_et():
    assert eval_metrics.to_int("3") == 3 and eval_metrics.to_int("") is None
    assert eval_metrics.to_int("abc") is None and eval_metrics.to_int(None) is None
    assert eval_metrics.norm_et(" Calc ") == "calc" and eval_metrics.norm_et("") is None


# ------------------------------------------------------------------ 指标计算

def test_objective_map_rate():
    rows = [
        _row(content_hash="h1", 题型="单选", 学生作答过程="选B"),
        _row(content_hash="h2", 题型="多选", 学生作答过程="选AC"),
        _row(content_hash="h3", 题型="解答", 学生作答过程="很长的过程文本超过十二个字"),
    ]
    results = [
        {"attribution_source": "map", "need_process": False},
        {"attribution_source": "llm", "need_process": False},     # 查表未命中走 LLM
        {"attribution_source": "llm", "need_process": False},
    ]
    m = eval_metrics.objective_map_rate(rows, results)
    assert m["total"] == 2 and m["map_hit"] == 1 and m["rate"] == 0.5


def test_first_wrong_step_accuracy_counts_full_correct_as_hit():
    rows = [
        _row(content_hash="h1", 题型="解答", 学生作答过程="p" * 20, 金标准首错步骤="2", 金标准错因="calc"),
        _row(content_hash="h2", 题型="解答", 学生作答过程="p" * 20),        # 金标准全对
        _row(content_hash="h3", 题型="解答", 学生作答过程="p" * 20, 金标准首错步骤="3", 金标准错因="read"),
    ]
    results = [
        {"first_wrong_step": 2, "need_process": False},
        {"first_wrong_step": None, "need_process": False},                   # 全对 = None 对齐
        {"first_wrong_step": 1, "need_process": False},                      # 判到 1，未对齐
        {"need_process": True},                                              # 不会到这行（rows 只有 3）
    ][:3]
    m = eval_metrics.first_wrong_step_accuracy(rows, results)
    assert m["total"] == 3 and m["hit"] == 2 and m["accuracy"] == round(2 / 3, 4)
    assert m["misses"] and m["misses"][0][1] == 3 and m["misses"][0][2] == 1


def test_first_wrong_step_skips_need_process():
    rows = [_row(content_hash="h1", 题型="解答", 学生作答过程="略")]
    results = [{"first_wrong_step": None, "need_process": True}]
    m = eval_metrics.first_wrong_step_accuracy(rows, results)
    assert m["total"] == 0 and m["skipped_need_process"] == 1 and m["accuracy"] is None


def test_error_type_metrics():
    rows = [
        _row(content_hash="h1", 题型="解答", 学生作答过程="p" * 20, 金标准错因="calc"),
        _row(content_hash="h2", 题型="解答", 学生作答过程="p" * 20, 金标准错因="read"),
        _row(content_hash="h3", 题型="单选", 学生作答过程="选A"),           # 全对行：不计准确率
    ]
    results = [
        {"error_type": "calc", "need_process": False},
        {"error_type": "unknown", "need_process": False},
        {"error_type": None, "need_process": False},
    ]
    m = eval_metrics.error_type_metrics(rows, results)
    assert m["judged"] == 3 and m["hit"] == 1 and m["labeled"] == 2
    assert m["unknown"] == 1 and m["unknown_rate"] == round(1 / 3, 4)
    assert m["confusion"] == {"read": {"unknown": 1}}


def test_cohens_kappa_perfect_and_partial():
    # 完全一致 -> κ=1
    assert eval_metrics.cohens_kappa(["calc", "read", "calc"], ["calc", "read", "calc"]) == 1.0
    # 部分一致：po=2/3
    k = eval_metrics.cohens_kappa(["calc", "read", "calc"], ["calc", "logic", "calc"])
    assert k is not None and -1.0 <= k <= 1.0
    # 边界：单样本 / 完全同分布（pe=1）-> None
    assert eval_metrics.cohens_kappa(["calc"], ["calc"]) is None


def test_rater_kappa_counts_only_rated_rows():
    rows = [
        _row(content_hash="h1", 学生作答过程="x", 评分人A="calc", 评分人B="calc"),
        _row(content_hash="h2", 学生作答过程="x", 评分人A="calc", 评分人B="logic"),
        _row(content_hash="h3", 学生作答过程="x"),                          # 双方皆空：不计
    ]
    m = eval_metrics.rater_kappa(rows)
    assert m["n"] == 2 and m["agree"] == 1 and m["po"] == 0.5


# ------------------------------------------------------------------ mock 判定器

def test_mock_judge_aligned_with_gold():
    row = _row(content_hash="h1", 题型="解答", 学生作答过程="p" * 20,
               金标准首错步骤="2", 金标准错因="calc")
    res = eval_metrics.mock_judge(row, noise=0.0)
    assert res["first_wrong_step"] == 2 and res["error_type"] == "calc"
    assert res["attribution_source"] == "llm"


def test_mock_judge_objective_uses_map_source():
    row = _row(content_hash="h2", 题型="单选", 学生作答过程="选B")
    res = eval_metrics.mock_judge(row, noise=0.0)
    assert res["attribution_source"] == "map"


def test_mock_judge_noise_changes_verdicts():
    rows = [
        _row(content_hash=f"h{i}", 题型="解答", 学生作答过程="p" * 20,
             金标准首错步骤="2", 金标准错因="calc")
        for i in range(20)
    ]
    noisy = [eval_metrics.mock_judge(r, noise=0.5) for r in rows]
    changed = sum(1 for r in noisy if r["first_wrong_step"] != 2)
    assert 0 < changed < len(rows)                       # 部分被扰动（非全真非全假）


# ------------------------------------------------------------------ 报告渲染

def test_render_report_contains_three_tables():
    rows = [_row(content_hash="h1", 题型="单选", 学生作答过程="选B", 评分人A="calc", 评分人B="calc")]
    results = [eval_metrics.mock_judge(rows[0])]
    obj = eval_metrics.objective_map_rate(rows, results)
    step = eval_metrics.first_wrong_step_accuracy(rows, results)
    et = eval_metrics.error_type_metrics(rows, results)
    kappa = eval_metrics.rater_kappa(rows)
    md = eval_metrics.render_report("测试批次", "g.csv", 1, obj, step, et, kappa)
    assert "表一" in md and "表二" in md and "表三" in md and "Cohen" in md
    assert "| 查表命中（map） | 1/1 = 100.0% | ✅ 达标" in md


# ------------------------------------------------------------------ 脚本全流程

def _load_script():
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import eval_attribution
    return eval_attribution


def test_script_mock_end_to_end(tmp_path):
    """脚手架自测跑通：合成金标准 → mock 判定 → 三张表 → 归档三件套。"""
    script = _load_script()
    gold = _write_csv(tmp_path / "gold.csv", [
        # 6 行中 5 行 calc、1 行 read（评分人完全一致但标签有变化，κ 才有定义）
        *[_row(content_hash=f"h{i}", 题型="解答", 学生作答过程="p" * 20,
               金标准首错步骤="2", 金标准错因="calc", 评分人A="calc", 评分人B="calc")
          for i in range(5)],
        _row(content_hash="h9", 题型="解答", 学生作答过程="p" * 20,
             金标准首错步骤="3", 金标准错因="read", 评分人A="read", 评分人B="read"),
    ])
    out = asyncio.run(script.run_eval(
        str(gold), batch="2026-脚手架自测", out_root=str(tmp_path / "eval"),
        mock=True, noise=0.0,
    ))
    # 归档三件套
    d = tmp_path / "eval" / "2026-脚手架自测"
    assert (d / "gold_standard.csv").exists()
    assert (d / "report.md").exists()
    details = json.loads((d / "results.json").read_text(encoding="utf-8"))
    assert len(details["rows"]) == 6
    # mock 无噪声时全对齐：首错步骤 6/6、错因 6/6、κ=1
    m = details["metrics"]
    assert m["first_wrong_step"]["hit"] == 6 and m["first_wrong_step"]["accuracy"] == 1.0
    assert m["error_type"]["hit"] == 6 and m["error_type"]["unknown_rate"] == 0.0
    assert m["rater_kappa"]["kappa"] == 1.0
    assert out["metrics"]["first_wrong_step"]["accuracy"] == 1.0


def test_script_mock_noise_moves_metrics(tmp_path):
    """有噪声时指标必须变化（防止指标恒真的假绿灯）。"""
    script = _load_script()
    gold = _write_csv(tmp_path / "gold.csv", [
        _row(content_hash=f"h{i}", 题型="解答", 学生作答过程="p" * 20,
             金标准首错步骤="2", 金标准错因="calc", 评分人A="calc", 评分人B="calc")
        for i in range(20)
    ])
    details = asyncio.run(script.run_eval(
        str(gold), batch="噪声批", out_root=str(tmp_path / "eval"),
        mock=True, noise=0.5,
    ))
    m = details["metrics"]
    assert m["first_wrong_step"]["accuracy"] < 1.0
    assert m["error_type"]["hit"] < 20
