"""M5-2 非结构化网页 -> 题目 LLM 抽取环节单测（transport 注入，不联网）。

覆盖：HTML 清洗与截断、稳健 JSON 解析（围栏/前言/尾注）、schema 校验与拒收、
默认关闭不触发模型、开启后成功抽取、模型/解析失败明确 reason、成本控制透传。
本仓库未装 pytest-asyncio，异步用例统一用 ``asyncio.run`` 驱动。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.crawl_service import fetcher as cf
from app.crawl_service import llm_extract as le


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- HTML 清洗
def test_clean_html_strips_script_style_and_nav():
    raw = (
        "<html><head><style>.x{color:red}</style><script>alert(1)</script></head>"
        "<body><nav>首页 | 栏目</nav><h1>第三章 练习题</h1>"
        "<p>1. 已知集合 A，求 A∩B。</p><p>2. 计算极限。</p>"
        "<footer>版权所有</footer></body></html>"
    )
    out = le.clean_html(raw)
    assert "alert(1)" not in out
    assert "color:red" not in out
    assert "首页" not in out          # nav 被丢弃
    assert "版权所有" not in out      # footer 被丢弃
    assert "第三章 练习题" in out
    assert "求 A∩B" in out
    assert "计算极限" in out


def test_clean_html_keeps_plain_text_and_unescapes():
    out = le.clean_html("题干：若 x &gt; 0，求 x 的范围   \n\n\n 答案：x&gt;0")
    assert "x > 0" in out
    assert "答案：x>0" in out
    assert "\n\n\n" not in out


def test_clean_html_truncates_for_cost_control():
    out = le.clean_html("题目内容" * 5000, max_chars=200)
    assert len(out) < 260
    assert "正文已截断" in out


def test_clean_html_handles_malformed_markup():
    out = le.clean_html("<div><p>未闭合的题<script>坏脚本")
    assert "未闭合的题" in out


# ----------------------------------------------------------------- 稳健 JSON
@pytest.mark.parametrize("text", [
    '{"questions":[{"stem":"s","answer":"a"}]}',
    '```json\n{"questions":[{"stem":"s","answer":"a"}]}\n```',
    '好的，以下是抽取结果：\n```json\n{"questions":[{"stem":"s","answer":"a"}]}\n```',
    '前言说明：{"questions":[{"stem":"s","answer":"a"}]} 以上为结果。',
    '[{"stem":"s","answer":"a"}]',
])
def test_parse_extract_json_robust(text):
    out = le.parse_extract_json(text)
    assert isinstance(out, (dict, list))


@pytest.mark.parametrize("text", ["", "完全没有 JSON 的一段话", "```json\n{bad json}\n```"])
def test_parse_extract_json_raises_on_garbage(text):
    with pytest.raises(ValueError):
        le.parse_extract_json(text)


# ----------------------------------------------------------------- schema 校验
def test_validate_extracted_accepts_valid_and_maps_chinese_keys():
    payload = {"questions": [
        {"题干": "求 A∩B", "答案": "B", "题型": "单选", "选项": ["A. 1", "B. 2"],
         "难度": "2", "知识点": ["集合"]},
        {"stem": "计算极限", "answer": "0", "type": "解答"},
    ]}
    items, rejected = le.validate_extracted(payload, max_items=20)
    assert len(items) == 2 and rejected == []
    assert items[0]["stem"] == "求 A∩B"
    assert items[0]["difficulty"] == 2
    assert items[0]["knowledge_points"] == ["集合"]


def test_validate_extracted_rejects_incomplete_and_duplicates():
    payload = {"questions": [
        {"stem": "", "answer": "A"},                 # 缺题干
        {"stem": "只有题干"},                        # 缺答案
        {"stem": "重复的题", "answer": "A"},
        {"stem": "重复的题", "answer": "A"},        # 同批重复
        {"stem": "正常题", "answer": "B", "type": "填空"},
        "不是对象",
    ]}
    items, rejected = le.validate_extracted(payload, max_items=20)
    assert len(items) == 2
    assert len(rejected) == 4
    assert any("缺题干或答案" in r["reason"] for r in rejected)
    assert any("重复" in r["reason"] for r in rejected)
    assert any("不是对象" in r["reason"] for r in rejected)


def test_validate_extracted_respects_max_items():
    payload = {"questions": [
        {"stem": f"题{i}", "answer": "A"} for i in range(10)
    ]}
    items, _ = le.validate_extracted(payload, max_items=3)
    assert len(items) == 3


def test_validate_extracted_non_array_payload():
    items, rejected = le.validate_extracted({"foo": "bar"}, max_items=20)
    assert items == [] and rejected


def test_validate_extracted_accepts_items_key():
    payload = {"items": [{"stem": "s", "answer": "a"}]}
    items, _ = le.validate_extracted(payload, max_items=20)
    assert len(items) == 1


# ----------------------------------------------------------------- 主入口
def test_extract_items_disabled_returns_clear_reason_without_calling_model():
    called = []

    async def transport(system, user):
        called.append(1)
        return "{}"

    items, reason = run(le.extract_items(
        "<html>正文</html>", source_type="web", transport=transport, enabled=False,
    ))
    assert items == []
    assert "未启用" in reason
    assert "LLM" in reason
    assert not called, "关闭时绝不能触发模型调用（省钱）"


def test_extract_items_success_with_injected_transport():
    captured = {}

    async def transport(system, user):
        captured["system"] = system
        captured["user"] = user
        return json.dumps({"questions": [
            {"stem": "已知集合 A={1,2}，B={2,3}，求 A∩B", "answer": "{2}",
             "type": "填空", "knowledge_points": ["集合运算"], "difficulty": 2},
        ]}, ensure_ascii=False)

    html = (
        "<html><script>ads()</script><body><h1>集合练习</h1>"
        "<p>已知集合 A={1,2}，B={2,3}，求 A∩B</p><p>答案：{2}</p></body></html>"
    )
    items, reason = run(le.extract_items(
        html, source_type="web", transport=transport, enabled=True,
    ))
    assert reason is None
    assert len(items) == 1
    assert items[0]["stem"].startswith("已知集合")
    assert items[0]["answer"] == "{2}"
    assert "严禁编造" in captured["system"]
    assert "集合练习" in captured["user"]        # 清洗后的正文进了 prompt
    assert "ads()" not in captured["user"]       # 脚本被清洗掉
    # 抽取结果必须能直接被 intake 入库（同一口径）
    from app.crawl_service.intake import normalize_item
    assert normalize_item(items[0], tenant_id="t1", grade="高三", subject="数学")


def test_extract_items_model_failure_returns_reason():
    async def transport(system, user):
        raise RuntimeError("模型端点返回 429")

    items, reason = run(le.extract_items(
        "<p>题目</p>", source_type="url", transport=transport, enabled=True,
    ))
    assert items == []
    assert "LLM 抽取失败" in reason and "429" in reason


def test_extract_items_bad_json_returns_reason():
    async def transport(system, user):
        return "抱歉，我无法完成该任务"

    items, reason = run(le.extract_items(
        "<p>题目</p>", source_type="url", transport=transport, enabled=True,
    ))
    assert items == [] and "LLM 抽取失败" in reason


def test_extract_items_no_questions_in_page_is_explicit():
    async def transport(system, user):
        return '{"questions": []}'

    items, reason = run(le.extract_items(
        "<p>本站是资讯首页，无试题</p>", source_type="web",
        transport=transport, enabled=True,
    ))
    assert items == []
    assert "未得到可入库题目" in reason


def test_extract_items_empty_content():
    items, reason = run(le.extract_items("", source_type="url", enabled=True))
    assert items == [] and "正文为空" in reason


def test_extract_items_passes_cost_limits_to_prompt_and_clean():
    captured = {}

    async def transport(system, user):
        captured["user"] = user
        return '{"questions":[{"stem":"s","answer":"a"}]}'

    items, _ = run(le.extract_items(
        "<p>题目" * 1000 + "</p>", source_type="web", transport=transport,
        enabled=True, max_chars=120, max_items=5,
    ))
    assert len(items) == 1
    assert "最多抽取】5 题" in captured["user"]
    assert "正文已截断" in captured["user"], "正文应按 max_chars 截断"
    assert len(captured["user"]) < 300, "prompt 不应带上万字符的原始正文"


# ----------------------------------------------------------------- 编排层
def test_needs_llm_marker_roundtrip():
    items, reason = cf.parse_items("<html>纯网页</html>", "web")
    assert items == [] and cf.needs_llm(reason)
    assert not cf.needs_llm(None)
    assert not cf.needs_llm("题库包不是合法 JSON")


def test_resolve_items_from_source_uses_llm_when_enabled(monkeypatch):
    """编排层：非结构化源在开关开启时转交 llm_extract，并把 items 交回。"""
    from app.crawl_service import scheduler
    from app.core.config import settings

    monkeypatch.setattr(settings, "crawl_llm_extract_enabled", True)

    async def fake_fetch(source, *, timeout=20.0):
        return "<html><p>题目</p></html>"

    async def fake_extract(content, **kwargs):
        assert kwargs["source_type"] == "web"
        return [{"stem": "抽取出的题", "answer": "A"}], None

    monkeypatch.setattr(scheduler.crawl_fetch, "fetch_source_content", fake_fetch)
    monkeypatch.setattr(scheduler.llm_extract, "extract_items", fake_extract)
    monkeypatch.setattr(
        scheduler.crawl_fetch, "needs_llm", lambda reason: True
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(source_type="web", uri="https://example.com/x")

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult()

    job = SimpleNamespace(source_id=1, tenant_id="school_pilot_01")
    items, reason = run(scheduler._resolve_items_from_source(FakeDB(), job))
    assert reason is None
    assert items == [{"stem": "抽取出的题", "answer": "A"}]


def test_resolve_items_from_source_bank_never_calls_llm(monkeypatch):
    """结构化 bank 源不得触发 LLM（避免无谓计费）。"""
    from app.crawl_service import scheduler

    called = []

    async def fake_fetch(source, *, timeout=20.0):
        return json.dumps([{"题干": "t", "答案": "a"}], ensure_ascii=False)

    async def fake_extract(content, **kwargs):
        called.append(1)
        return [], "不该被调用"

    monkeypatch.setattr(scheduler.crawl_fetch, "fetch_source_content", fake_fetch)
    monkeypatch.setattr(scheduler.llm_extract, "extract_items", fake_extract)

    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(source_type="bank_pack", uri="/opt/course-sources/b.json")

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult()

    job = SimpleNamespace(source_id=1, tenant_id="school_pilot_01")
    items, reason = run(scheduler._resolve_items_from_source(FakeDB(), job))
    assert reason is None and len(items) == 1 and not called
