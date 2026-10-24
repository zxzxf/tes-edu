"""T-A1 教材题加工单测（本地直跑，不依赖 DB）。

锁住三件事：
  1. 正常题 -> 16 字段 + 步骤结构正确（单选走 option 步、按年级映射）
  2. 缺必填字段（知识点/题干/题型）-> 抛 ValueError，清晰报错而非静默 None
  3. build_textbook 能把坏题记为 rejects，好题照常产出
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_for_import as ex  # noqa: E402


def _prob(**kw):
    base = {
        "no": 1, "type": "单选", "stem": "x", "options": ["A. a", "B. b"],
        "answer": "A", "analysis": "ok",
        "knowledge_points": ["MA-X-K01"], "difficulty": 1,
    }
    base.update(kw)
    return base


def test_normalize_ok_option_steps_and_grade():
    meta = {"book": "必修第一册", "chapter": "第一章", "section": "1.1"}
    row = ex.normalize_textbook_problem(_prob(type="单选"), meta, {}, "数学")
    assert row["题型"] == "单选"
    assert row["年级"] == "高一"
    assert [s["step_kind"] for s in row["步骤"]] == ["option", "option"]


def test_normalize_solve_splits_procedure():
    meta = {"book": "选择性必修第三册", "chapter": "第六章", "section": "6.2"}
    p = _prob(type="解答",
              analysis="（1）求导；（2）令导数为0；（3）列表判断。")
    row = ex.normalize_textbook_problem(p, meta, {}, "数学")
    assert row["题型"] == "解答"
    assert row["年级"] == "高三"
    kinds = [s["step_kind"] for s in row["步骤"]]
    assert "procedure" in kinds


def test_missing_kp_rejected():
    meta = {"book": "", "chapter": "", "section": "s"}
    try:
        ex.normalize_textbook_problem(_prob(knowledge_points=[]), meta, {}, "数学")
        raise AssertionError("应抛 ValueError")
    except ValueError as e:
        assert "知识点" in str(e)


def test_missing_stem_rejected():
    meta = {"book": "", "chapter": "", "section": "s"}
    try:
        ex.normalize_textbook_problem(_prob(stem=""), meta, {}, "数学")
        raise AssertionError("应抛 ValueError")
    except ValueError as e:
        assert "题干" in str(e)


def test_build_textbook_rejects_bad_keeps_good():
    src = [{
        "book": "必修第一册", "chapter": "c", "section": "s",
        "problems": [_prob(knowledge_points=[]), _prob()],
    }]
    fd, path = tempfile.mkstemp(suffix=".json", prefix="tb_")
    os.write(fd, json.dumps(src, ensure_ascii=False).encode("utf-8"))
    os.close(fd)
    try:
        rows, rejects = ex.build_textbook(path, "数学")
    finally:
        os.unlink(path)
    assert len(rows) == 1
    assert len(rejects) == 1
    assert "知识点" in rejects[0]["reason"]
