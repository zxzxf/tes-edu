"""import_bank.py 的回归测试（纯 stdlib，不连库、不装业务依赖）。

保护 3000 题灌库流水线的解析/映射/归一化逻辑，避免后续改动静默破坏列映射。
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


def _rows():
    raw = open(DEMO, "rb").read()
    return ib._parse_file(raw, "demo.csv")


def test_parse_demo_row_count():
    assert len(_rows()) == 7


def test_header_map_includes_stem():
    hm = ib._build_header_map(list(_rows()[0].keys()))
    assert hm["stem"] == "题干"
    # BOM 兼容：首列不能是 题干
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


def test_dry_run_subprocess():
    """--dry-run 不连库、不写数据，仅解析校验。"""
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "import_bank.py"),
         DEMO, "--dry-run", "--source", "河南", "--paper", "新高考I卷"],
        capture_output=True, text=True,
        cwd=os.path.join(REPO_ROOT, "agent-backend"),
    )
    assert r.returncode == 0, r.stderr
    assert "归一化后有效题目：7" in r.stdout
    assert "[dry-run] 未写入数据库。" in r.stdout
