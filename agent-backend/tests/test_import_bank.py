"""import_bank.py 的回归测试（纯 stdlib，不连库、不装业务依赖）。

保护 3000 题灌库流水线的解析/映射/归一化逻辑，避免后续改动静默破坏列映射；
并覆盖 S1 验收项「列数≠表头即拒收该行并报告」的闸门行为。
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import import_bank as ib  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEMO = os.path.join(REPO_ROOT, "agent-backend", "data", "incoming", "demo_2025_igaokaoI.csv")
SCRIPT = os.path.join(SCRIPTS, "import_bank.py")
CWD = os.path.join(REPO_ROOT, "agent-backend")


def _parse_demo():
    raw = open(DEMO, "rb").read()
    return ib._parse_file(raw, "demo.csv")


def _rows():
    return _parse_demo()[0]


def _run_cli(path, *extra):
    return subprocess.run(
        [sys.executable, SCRIPT, str(path), "--dry-run", *extra],
        capture_output=True, text=True, cwd=CWD,
    )


def test_parse_demo_row_count():
    assert len(_rows()) == 7


def test_header_map_includes_stem():
    hm = ib._build_header_map(list(_rows()[0].keys()))
    assert hm["stem"] == "题干"
    # BOM 兼容：首列不能是 ﻿题干
    assert not any(k.startswith("﻿") for k in hm.values())


def test_kp_split():
    assert ib._split_kp("同余方程；数列求和") == ["同余方程", "数列求和"]
    assert ib._split_kp("a/b、c|d，e") == ["a", "b", "c", "d", "e"]
    assert ib._split_kp(None) == []
    assert ib._split_kp("") == []


def test_norm_type():
    assert ib._norm_type("选择题") == "选择"
    assert ib._norm_type("填空题") == "填空"
    assert ib._norm_type("压轴题") == "压轴"
    assert ib._norm_type("") == "解答"


def test_norm_difficulty():
    assert ib._norm_difficulty("基础") == 1
    assert ib._norm_difficulty("中档") == 3
    assert ib._norm_difficulty("难") == 5
    assert ib._norm_difficulty("4") == 4
    assert ib._norm_difficulty("") == 3


# ---------------------------------------------------------------------------
# S1 闸门：列数校验
# ---------------------------------------------------------------------------
def test_gate_rejects_misaligned_rows():
    """列数≠表头的行必须被拒收并报告，绝不能静默错位入库。"""
    text = (
        "题干,题型,答案,解析\n"
        "正常题干,单选,A,解析一\n"
        "缺列题干,单选,A\n"                 # 少一列
        "超列题干,单选,A,解析二,多余内容\n"  # 多一列
    )
    recs, rejects = ib._parse_file(text.encode("utf-8"), "t.csv")
    assert len(recs) == 1, "只有列数正确的行应通过"
    assert len(rejects) == 2
    assert {r["reason"] for r in rejects} == {"列数不符"}
    assert [r["line"] for r in rejects] == [3, 4]  # 行号含表头，从 2 起算
    assert "表头 4 列" in rejects[0]["detail"]


def test_gate_passes_wellformed_rows():
    text = "题干,题型,答案,解析\n题一,单选,A,解析一\n题二,填空,3,解析二\n"
    recs, rejects = ib._parse_file(text.encode("utf-8"), "t.csv")
    assert len(recs) == 2
    assert rejects == []


def test_gate_ignores_blank_lines():
    """文件末尾空行不算拒收，否则每次导入都会报错扰民。"""
    text = "题干,题型,答案,解析\n题一,单选,A,解析一\n\n"
    recs, rejects = ib._parse_file(text.encode("utf-8"), "t.csv")
    assert len(recs) == 1
    assert rejects == []


def test_demo_has_no_misaligned_rows():
    recs, rejects = _parse_demo()
    assert rejects == [], f"demo 不该有列数不符的行：{rejects}"


# ---------------------------------------------------------------------------
# 选项结构化（旧实现会把 CSV 里的整串选项丢成 None）
# ---------------------------------------------------------------------------
def test_parse_options_splits_by_label_with_comma_inside():
    assert ib._parse_options("A.2,−4 B.2,5 C.−2,4 D.−2,5") == {
        "A": "2,−4", "B": "2,5", "C": "−2,4", "D": "−2,5",
    }
    assert ib._parse_options("A.{1} B.{1,2} C.{3,4}") == {
        "A": "{1}", "B": "{1,2}", "C": "{3,4}",
    }


def test_parse_options_rejects_unparseable():
    assert ib._parse_options("A B C D") is None   # 无点号标签，宁可留空也不猜
    assert ib._parse_options(None) is None
    assert ib._parse_options("") is None


def test_parse_options_accepts_structs():
    assert ib._parse_options({"A": "1", "B": "2"}) == {"A": "1", "B": "2"}
    assert ib._parse_options([{"label": "A", "text": "1"}]) == {"A": "1"}


# ---------------------------------------------------------------------------
# 假元数据拦截
# ---------------------------------------------------------------------------
def test_validate_row_rejects_placeholder_answer():
    assert ib._validate_row({"type": "单选", "answer": "图示题演示略", "stem": "x"})
    assert ib._validate_row({"type": "单选", "answer": "B演示待核", "stem": "x"})
    assert ib._validate_row({"type": "单选", "answer": "AB", "stem": "x"})  # 单选多答案
    assert ib._validate_row({"type": "单选", "answer": "", "stem": "x"})    # 缺答案


def test_validate_row_accepts_valid():
    assert ib._validate_row({"type": "单选", "answer": "A", "stem": "x"}) is None
    assert ib._validate_row({"type": "多选", "answer": "AB", "stem": "x"}) is None
    assert ib._validate_row({"type": "解答", "answer": "任意文字答案", "stem": "x"}) is None


def test_demo_has_no_placeholder_metadata():
    """demo 里的假元数据（如 'B演示待核'）应已修复，不再被第二道闸拒收。"""
    hm = ib._build_header_map(list(_rows()[0].keys()))
    bad = []
    for r in _rows():
        row = {f: r.get(src) for f, src in hm.items()}
        row["type"] = ib._norm_type(row.get("type"))
        reason = ib._validate_row(row)
        if reason:
            bad.append((str(row.get("stem"))[:20], reason))
    assert bad == [], f"demo 仍含假元数据：{bad}"


# ---------------------------------------------------------------------------
# CLI 行为
# ---------------------------------------------------------------------------
def test_dry_run_subprocess():
    """--dry-run 不连库、不写数据，仅解析校验。"""
    r = _run_cli(DEMO, "--source", "河南", "--paper", "新高考I卷")
    assert r.returncode == 0, r.stderr
    assert "归一化后有效题目：7" in r.stdout
    assert "[dry-run] 未写入数据库。" in r.stdout


def test_cli_reports_gate_rejections(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("题干,题型,答案,解析\n正常题,单选,A,解析\n错位题,单选,A\n", encoding="utf-8")
    r = _run_cli(p)
    assert r.returncode == 0, "默认模式只拒收并报告，不应失败退出"
    assert "闸门" in r.stdout
    assert "列数不符" in r.stdout


def test_cli_line_numbers_not_shifted_by_rejections(tmp_path):
    """被拒收的行不占位，后续行的行号仍须是文件中的真实行号。"""
    p = tmp_path / "mixed.csv"
    p.write_text(
        "题干,题型,答案,解析\n"
        "正常题,单选,A,解析一\n"
        "缺列题,单选,A\n"                  # 第 3 行：列数不符
        "假数据题,单选,图示题演示略,解析三\n"  # 第 4 行：答案非法
        , encoding="utf-8",
    )
    r = _run_cli(p)
    assert "第 3 行" in r.stdout and "列数不符" in r.stdout
    assert "第 4 行" in r.stdout and "答案非法" in r.stdout


def test_cli_strict_aborts_on_rejection(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("题干,题型,答案,解析\n正常题,单选,A,解析\n错位题,单选,A\n", encoding="utf-8")
    r = _run_cli(p, "--strict")
    assert r.returncode != 0, "--strict 下存在拒收行必须中止"
    assert "闸门" in r.stdout


@pytest.mark.parametrize("answer,should_pass", [
    ("A", True), ("AB", False), ("图示题演示略", False), ("", False),
])
def test_cli_rejects_fake_answer(tmp_path, answer, should_pass):
    p = tmp_path / "q.csv"
    p.write_text(f"题干,题型,答案,解析\n某题,单选,{answer},某解析\n", encoding="utf-8")
    r = _run_cli(p)
    passed = "[闸门]" not in r.stdout
    assert passed == should_pass, f"answer={answer!r} stdout={r.stdout}"
