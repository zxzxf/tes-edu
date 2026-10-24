"""S5 聚合与报告：纯函数层 + 周报步骤级薄弱点。

沿用 web-backend 测试哲学：不连库、不发请求。行数据用 SimpleNamespace 模拟 ORM 对象
（聚合层同时支持 dict 与 ORM 对象，两种都要覆盖到）。
"""
from types import SimpleNamespace as NS
from datetime import datetime, timedelta, timezone

from app.services import attribution, reports


def _row(hash="h1", step=2, etype="case", src="map", sid="s1", days_ago=1):
    return NS(
        student_id=sid,
        content_hash=hash,
        step_no=step,
        error_type=etype,
        attribution_source=src,
        evidence="漏了 a=0 的情况",
        occurred_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------- 归一化

def test_normalize_error_type_accepts_various_forms():
    assert attribution.normalize_error_type("case") == "case"
    assert attribution.normalize_error_type("CASE") == "case"
    assert attribution.normalize_error_type(" 概念理解 ") == "concept"
    assert attribution.normalize_error_type("计算错误") == "calc"
    assert attribution.normalize_error_type("分类讨论遗漏") == "case"
    assert attribution.normalize_error_type(None) == "unknown"
    assert attribution.normalize_error_type("不存在的类型") == "unknown"


def test_label_and_advice():
    assert attribution.label_of("calc") == "计算运算错误"
    assert attribution.label_of("概念混淆") == "概念理解错误"
    assert attribution.advice_of("unknown") == attribution.ERROR_TYPE_ADVICE["unknown"]


# ---------------------------------------------------------------- 错因分布

def test_count_by_error_type_order_and_ratio():
    rows = [_row(etype="case"), _row(etype="case"), _row(etype="calc"), _row(etype="概念混淆")]
    dist = attribution.count_by_error_type(rows)
    # 次数降序；并列（calc 与 concept 各 1 次）按七类固定顺序，保证老师端对账可复现
    assert [d["error_type"] for d in dist] == ["case", "concept", "calc"]
    assert dist[0]["count"] == 2 and dist[0]["ratio"] == 0.5
    assert dist[0]["label"] == "分类讨论遗漏"
    assert sum(d["count"] for d in dist) == 4


def test_count_by_error_type_empty():
    assert attribution.count_by_error_type([]) == []


def test_rows_can_be_dict():
    dist = attribution.count_by_error_type([{"error_type": "read"}, {"error_type": "read"}])
    assert dist == [
        {
            "error_type": "read",
            "label": "审题漏条件",
            "count": 2,
            "ratio": 1.0,
            "advice": attribution.ERROR_TYPE_ADVICE["read"],
        }
    ]


# ---------------------------------------------------------------- 断点步骤

def test_top_break_steps_aggregates_same_step():
    rows = [
        _row(hash="h1", step=2, etype="case"),
        _row(hash="h1", step=2, etype="case"),
        _row(hash="h1", step=3, etype="calc"),
        _row(hash="h2", step=1, etype="logic"),
    ]
    step_texts = {("h1", 2): {"text": "讨论 a 是否为 0", "label": "A", "kp_name": "二次函数"}}
    top = attribution.top_break_steps(rows, step_texts, top_n=2)
    assert len(top) == 2
    assert top[0]["content_hash"] == "h1" and top[0]["step_no"] == 2
    assert top[0]["count"] == 2 and top[0]["main_error_type"] == "case"
    assert top[0]["step_label"] == "第2步 · 讨论 a 是否为 0"
    assert top[0]["kp_name"] == "二次函数"
    assert top[1]["count"] == 1


def test_top_break_steps_without_step_text():
    top = attribution.top_break_steps([_row(hash="h1", step=0)], None)
    assert top[0]["step_label"] == "未定位步骤"
    assert top[0]["step_no"] == 0


def test_top_break_steps_ignores_rows_without_hash():
    assert attribution.top_break_steps([_row(hash="")], None) == []


# ---------------------------------------------------------------- 周报步骤级薄弱点

def test_build_step_weakness_headline():
    rows = [_row(hash="h1", step=2, etype="case")] * 3 + [_row(hash="h1", step=3, etype="calc")]
    step_texts = {("h1", 2): {"text": "讨论 a 是否为 0", "label": "A", "kp_name": "二次函数"}}
    w = attribution.build_step_weakness(rows, step_texts)
    assert w["total_errors"] == 4
    assert "第2步 · 讨论 a 是否为 0" in w["headline"]
    assert "错了 3 次" in w["headline"]
    assert w["error_types"][0]["error_type"] == "case"
    assert w["advice"]


def test_build_step_weakness_empty():
    w = attribution.build_step_weakness([])
    assert w == {"total_errors": 0, "error_types": [], "top_steps": [], "headline": "", "advice": []}


# ---------------------------------------------------------------- 老师端分布

def test_build_class_distribution_by_error_type():
    rows = [_row(etype="case")] * 3 + [_row(etype="calc")]
    d = attribution.build_class_distribution(rows, "error_type")
    assert d["total"] == 4
    assert d["rows"][0]["key"] == "case" and d["rows"][0]["ratio"] == 0.75
    assert d["rows"][0]["advice"]


def test_build_class_distribution_by_question():
    rows = [_row(hash="h1")] * 2 + [_row(hash="h2")]
    titles = {"h1": "已知函数 f(x)=ax^2+bx+c，讨论其单调性", "h2": "求极限"}
    d = attribution.build_class_distribution(rows, "question", question_titles=titles, top_n=10)
    assert d["rows"][0]["key"] == "h1" and d["rows"][0]["count"] == 2
    assert d["rows"][0]["student_count"] == 1
    assert "已知函数" in d["rows"][0]["label"]


def test_build_class_distribution_by_step():
    rows = [_row(hash="h1", step=2)] * 3 + [_row(hash="h1", step=3)]
    d = attribution.build_class_distribution(rows, "step", top_n=10)
    assert d["rows"][0]["content_hash"] == "h1"
    assert d["rows"][0]["step_no"] == 2
    assert d["rows"][0]["count"] == 3


def test_build_class_distribution_falls_back_to_error_type():
    d = attribution.build_class_distribution([_row()], "不存在的维度")
    assert d["group_by"] == "error_type"


# ---------------------------------------------------------------- 归因来源

def test_attribution_source_ratio():
    rows = [_row(src="map")] * 7 + [_row(src="llm")] * 2 + [_row(src="unknown")]
    r = attribution.attribution_source_ratio(rows)
    assert r["total"] == 10
    assert r["counts"] == {"map": 7, "llm": 2, "unknown": 1}
    assert r["deterministic_ratio"] == 0.7
    assert r["unknown_ratio"] == 0.1


def test_attribution_source_alias():
    r = attribution.attribution_source_ratio([NS(attribution_source="table")])
    assert r["counts"]["map"] == 1


def test_metrics_payload_is_anonymous():
    payload = attribution.metrics_payload([_row(sid="私密ID")])
    assert "私密ID" not in str(payload)
    assert payload["attribution_total"] == 1


# ---------------------------------------------------------------- 错题本 correction

def test_pick_correction_from_typical_errors_matching_type():
    step = NS(
        typical_errors=[
            {"error_type": "calc", "reason": "符号错", "correction": "注意负号"},
            {"error_type": "case", "reason": "漏讨论", "correction": "分 a=0 与 a≠0"},
        ],
        distractor_map=None,
        wrong_value_map=None,
        error_reason="",
        text="",
    )
    assert attribution.pick_correction(step, "case") == "分 a=0 与 a≠0"
    # 不匹配时退化为第一条
    assert attribution.pick_correction(step, "read") == "注意负号"


def test_pick_correction_from_distractor_map():
    step = NS(
        typical_errors=None,
        distractor_map={"error_type": "concept", "correction": "中位数看位置不看大小"},
        wrong_value_map=None,
        error_reason="",
        text="",
    )
    assert attribution.pick_correction(step, "concept") == "中位数看位置不看大小"


def test_pick_correction_reports_whether_matched():
    step = NS(
        typical_errors=[{"error_type": "calc", "correction": "注意负号"}],
        distractor_map=None, wrong_value_map=None, error_reason="", text="",
    )
    # 请求 case 但只有 calc 的映射 → 退化返回，且 matched=False（展示侧要改文案）
    assert attribution.pick_correction_detail(step, "case") == ("注意负号", False)
    assert attribution.pick_correction_detail(step, "calc") == ("注意负号", True)
    assert attribution.pick_correction(step, "case") == "注意负号"


def test_pick_correction_falls_back_to_error_reason():
    step = NS(
        typical_errors=None, distractor_map=None, wrong_value_map=None,
        error_reason="这一步容易把定义域漏掉", text="",
    )
    assert attribution.pick_correction(step, "calc") == "这一步容易把定义域漏掉"


def test_pick_correction_none_when_no_step():
    assert attribution.pick_correction(None, "calc") is None


def test_build_wrong_question_enrich():
    step = NS(
        text="求导数", label="(1)", kp_name="导数", explanation="链式法则",
        typical_errors=[{"error_type": "formula", "correction": "先外层后内层"}],
        distractor_map=None, wrong_value_map=None, error_reason="",
    )
    card = NS(understanding="导数是变化率", memory="链式：由外到内")
    e = attribution.build_wrong_question_enrich(step, "公式误用", card)
    assert e["error_type"] == "formula" and e["error_type_label"] == "公式误用"
    assert e["correction"] == "先外层后内层"
    assert e["understanding"] == "导数是变化率" and e["memory"] == "链式：由外到内"
    assert e["kp_name"] == "导数"


def test_build_wrong_question_enrich_without_step():
    e = attribution.build_wrong_question_enrich(None, None, None)
    assert e["error_type"] == "unknown"
    assert e["correction"] is None


# ---------------------------------------------------------------- CSV 导出

def test_distribution_csv_three_shapes():
    rows = [_row(hash="h1", step=2)] * 2
    for gb, first in (("error_type", "错因类型"), ("question", "题目(content_hash)"), ("step", "题目(content_hash)")):
        d = attribution.build_class_distribution(rows, gb, top_n=5)
        csv_text = attribution.distribution_csv(d)
        assert csv_text.splitlines()[0].startswith(first)


def test_to_csv_quotes_and_none():
    csv_text = attribution.to_csv(["a", "b"], [[1, None], ["x,y", "z"]])
    assert 'x,y' in csv_text
    assert csv_text.splitlines()[1] == "1,"


# ---------------------------------------------------------------- 周报集成

def _mastery(kp, c=1, w=1):
    return NS(
        student_id="s1", tenant_id=None, subject="数学", knowledge_point=kp,
        practice_count=c + w, correct_count=c, wrong_count=w,
        last_practiced_at=datetime.now(timezone.utc),
    )


def test_report_includes_step_weakness():
    """3 学生×10 事件口径下的单学生聚合（计划书验收标准）。"""
    now = datetime.now(timezone.utc)
    events = [
        NS(event_type="solve", subject="数学", question="q", knowledge_points=[],
           occurred_at=now - timedelta(days=1))
    ]
    step_errors = [_row(hash="h1", step=2, etype="case")] * 3 + [
        _row(hash="h1", step=3, etype="calc")
    ] * 2 + [_row(hash="h2", step=1, etype="read")]
    step_texts = {("h1", 2): {"text": "讨论 a 是否为 0", "label": "A", "kp_name": "二次函数"}}

    rep = reports._build_report(
        "s1", [_mastery("二次函数")], events, 7, now,
        step_errors=step_errors, step_texts=step_texts,
    )
    sw = rep["step_weakness"]
    assert sw["total_errors"] == 6
    assert sw["error_types"][0]["error_type"] == "case"
    assert sw["top_steps"][0]["step_no"] == 2 and sw["top_steps"][0]["count"] == 3
    assert "步骤级薄弱点" in rep["summary"] or "错因 6 次" in rep["summary"]
    # 结论进建议，且排在最前
    assert rep["suggestions"][0] == sw["headline"]


def test_report_backward_compatible_without_step_errors():
    now = datetime.now(timezone.utc)
    rep = reports._build_report("s1", [_mastery("函数")], [], 7, now)
    assert rep["step_weakness"]["total_errors"] == 0
    assert "step_weakness" in rep


def test_report_html_renders_step_weakness_section():
    now = datetime.now(timezone.utc)
    rep = reports._build_report(
        "s1", [_mastery("函数")], [], 7, now,
        step_errors=[_row(hash="h1", step=2, etype="case")] * 2,
        step_texts={("h1", 2): {"text": "讨论 a 是否为 0", "label": "A", "kp_name": "二次函数"}},
    )
    html = reports._render_html(rep)
    assert "步骤级薄弱点" in html
    assert "分类讨论遗漏" in html
    assert "第2步 · 讨论 a 是否为 0" in html


def test_report_html_renders_empty_step_weakness():
    now = datetime.now(timezone.utc)
    rep = reports._build_report("s1", [], [], 7, now)
    html = reports._render_html(rep)
    assert "步骤级薄弱点" in html
    assert "本周暂无步骤级错因记录" in html
