"""M5 采集源抓取与解析链路单测（让「采集任务」从空跑到可用）。

覆盖：bank 中->英映射、JSON 列表解析、非结构化内容明确原因、SSRF 防护拦截
私网/回环/云元数据、本地文件读取（防穿越）、以及 fetch+parse+normalize 闭环。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.crawl_service import fetcher as cf
from app.crawl_service.intake import normalize_item


# ----------------------------------------------------------------- 映射
def test_map_bank_record_chinese_keys():
    rec = {
        "学科": "数学", "年级": "高三", "章节": "集合", "题型": "单选",
        "题干": "求 A∩B", "选项": ["A. 1", "B. 2"], "答案": "B",
        "解析": "略", "难度": "2", "卷别": "新高考I卷",
        "来源省份": "广东", "知识点": ["MA-K01", "MA-K02"],
    }
    out = cf.map_bank_record(rec)
    assert out["stem"] == "求 A∩B"
    assert out["answer"] == "B"
    assert out["type"] == "单选"
    assert out["options"] == ["A. 1", "B. 2"]
    assert out["difficulty"] == 2
    assert out["knowledge_points"] == ["MA-K01", "MA-K02"]
    assert out["subject"] == "数学"
    assert out["grade"] == "高三"


def test_map_bank_record_english_keys_passthrough():
    rec = {"stem": "s", "answer": "a", "type": "填空", "knowledge_points": "K1,K2"}
    out = cf.map_bank_record(rec)
    assert out["stem"] == "s"
    assert out["knowledge_points"] == ["K1", "K2"]  # 字符串按分隔符拆


def test_map_bank_record_options_dict():
    out = cf.map_bank_record({"题干": "x", "答案": "y", "选项": {"A": "1", "B": "2"}})
    assert out["options"] == ["A. 1", "B. 2"]


# ----------------------------------------------------------------- 解析
def test_parse_items_bank_list():
    content = json.dumps([
        {"学科": "数学", "年级": "高三", "题干": "题1", "答案": "A", "题型": "单选",
         "选项": ["A. 1"], "解析": "r", "难度": "3", "知识点": ["K1"]},
        {"学科": "数学", "年级": "高三", "题干": "题2", "答案": "B", "题型": "填空",
         "解析": "r", "难度": "4", "知识点": ["K2"]},
    ], ensure_ascii=False)
    items, reason = cf.parse_items(content, "bank_pack")
    assert reason is None
    assert len(items) == 2
    assert items[0]["stem"] == "题1"
    assert items[1]["difficulty"] == 4


def test_parse_items_bank_wrapped_object():
    content = json.dumps({"items": [{"题干": "t", "答案": "a"}]}, ensure_ascii=False)
    items, reason = cf.parse_items(content, "bank")
    assert reason is None and len(items) == 1


def test_parse_items_bank_invalid_json():
    items, reason = cf.parse_items("not json at all", "bank_pack")
    assert items == [] and "合法 JSON" in reason


def test_parse_items_non_json_html_explains_llm_needed():
    items, reason = cf.parse_items("<html>一堆正文</html>", "url")
    assert items == [] and "LLM" in reason


def test_parse_items_non_bank_json_list_still_parsed():
    content = json.dumps([{"题干": "t", "答案": "a"}], ensure_ascii=False)
    items, reason = cf.parse_items(content, "file")
    assert reason is None and len(items) == 1


# ----------------------------------------------------------------- SSRF
@pytest.mark.parametrize("host,ok", [
    ("localhost", False), ("0.0.0.0", False), ("::1", False),
    ("10.0.0.5", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("127.0.0.1", False), ("example.com", True),
])
def test_is_safe_host(host, ok):
    assert cf._is_safe_host(host) is ok


def test_resolve_safe_blocks_private_and_local():
    for bad in ("http://127.0.0.1/x", "http://localhost/x",
                "http://10.0.0.5/x", "http://169.254.169.254/x",
                "ftp://example.com/x"):
        with pytest.raises(ValueError):
            cf._resolve_safe(bad)
    # 公网域名 / IP 放行（example.com 解析为公网 IP）
    assert cf._resolve_safe("https://example.com/x") == "https://example.com/x"


# ----------------------------------------------------------------- 本地文件
def test_read_local_file_within_root(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "COURSE_SOURCE_ROOT", str(tmp_path))
    f = tmp_path / "bank.json"
    f.write_text("[{\"题干\":\"t\",\"答案\":\"a\"}]", encoding="utf-8")
    assert "题干" in cf.read_local_file(str(f))


def test_read_local_file_blocks_traversal(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.json"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setattr(cf, "COURSE_SOURCE_ROOT", str(root))
    with pytest.raises(ValueError):
        cf.read_local_file(str(outside))


# ----------------------------------------------------------------- 源类型路由
def test_fetch_source_content_web_url_goes_ssrf(monkeypatch):
    """web + http(s) URL 必须走 SSRF 安全抓取，不再误读本地文件（M5-2 前置修复）。"""
    seen = {}

    async def fake_fetch(url, timeout=20.0):
        seen["url"] = url
        seen["timeout"] = timeout
        return "<html>网页正文</html>"

    monkeypatch.setattr(cf, "safe_fetch_url", fake_fetch)
    src = SimpleNamespace(source_type="web", uri="https://www.pep.com.cn/")
    content = asyncio.run(cf.fetch_source_content(src))
    assert content == "<html>网页正文</html>"
    assert seen["url"] == "https://www.pep.com.cn/"


def test_fetch_source_content_web_local_path_still_reads_file(tmp_path, monkeypatch):
    """历史 web 源指向本地文件时保持兼容，不破坏存量数据。"""
    monkeypatch.setattr(cf, "COURSE_SOURCE_ROOT", str(tmp_path))
    f = tmp_path / "page.html"
    f.write_text("<html>本地缓存页</html>", encoding="utf-8")
    src = SimpleNamespace(source_type="web", uri=str(f))
    assert "本地缓存页" in asyncio.run(cf.fetch_source_content(src))


def test_fetch_source_content_url_rejects_non_http():
    src = SimpleNamespace(source_type="url", uri="ftp://example.com/x")
    with pytest.raises(ValueError):
        asyncio.run(cf.fetch_source_content(src))


def test_parse_items_needs_llm_marker():
    items, reason = cf.parse_items("<html>正文</html>", "web")
    assert items == [] and cf.needs_llm(reason)
    assert "尚未接入" not in reason, "功能已落地，文案不应再写「尚未接入」"


# ----------------------------------------------------------------- 闭环
def test_fetch_parse_normalize_closed_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "COURSE_SOURCE_ROOT", str(tmp_path))
    bank = tmp_path / "b.json"
    bank.write_text(json.dumps([
        {"学科": "数学", "年级": "高三", "题干": "集合 A∩B", "答案": "B",
         "题型": "单选", "选项": ["A. 1", "B. 2"], "解析": "r",
         "难度": "2", "知识点": ["MA-K01"]}
    ], ensure_ascii=False), encoding="utf-8")
    src = SimpleNamespace(source_type="bank_pack", uri=str(bank))
    content = cf.read_local_file(src.uri)  # 直接走本地读取分支
    items, reason = cf.parse_items(content, src.source_type)
    assert reason is None and len(items) == 1
    norm = normalize_item(items[0], tenant_id="t1", grade="高三", subject="数学")
    assert norm is not None
    assert norm["stem"] == "集合 A∩B"
    assert norm["source_tag"] == "auto_crawl"
    assert norm["status"] == "draft"


# ------------------------------------------- 抓取加固（2026-11-12 真站点验收）
class _FakeResp:
    def __init__(self, status_code=200, headers=None, text="ok"):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _install_fake_client(monkeypatch, responses, captured):
    """把 httpx.AsyncClient 换成可控假客户端（单测不联网）。"""
    import httpx

    class FakeClient:
        def __init__(self, **kw):
            captured["kwargs"] = kw
            captured["urls"] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            captured["urls"].append(url)
            return responses.pop(0) if len(responses) > 1 else responses[0]

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


def test_safe_fetch_url_browser_ua_and_manual_redirect(monkeypatch):
    """带浏览器 UA（避免 403）+ 关闭自动重定向（逐跳 SSRF 复查）。"""
    captured = {}
    _install_fake_client(monkeypatch, [_FakeResp(text="<html>ok</html>")], captured)
    monkeypatch.setattr(cf, "_resolve_safe", lambda u: u)
    assert asyncio.run(cf.safe_fetch_url("https://example.com/page")) == "<html>ok</html>"
    kw = captured["kwargs"]
    assert "Mozilla" in kw["headers"]["User-Agent"]
    assert kw["follow_redirects"] is False


def test_safe_fetch_url_blocks_private_redirect(monkeypatch):
    """302 到云元数据必须在发出第二跳之前被拦（旧 follow_redirects 会绕过）。"""
    captured = {}
    _install_fake_client(
        monkeypatch,
        [
            _FakeResp(302, {"location": "http://169.254.169.254/latest/meta-data/"}),
            _FakeResp(text="SHOULD_NOT_REACH"),
        ],
        captured,
    )
    monkeypatch.setattr(
        cf.socket, "getaddrinfo",
        lambda host, *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )
    with pytest.raises(ValueError, match="私网|元数据|本地"):
        asyncio.run(cf.safe_fetch_url("https://example.com/page"))
    assert len(captured["urls"]) == 1, "私网重定向不应产生第二次请求"


def test_safe_fetch_url_follows_public_redirect(monkeypatch):
    """公网重定向仍正常跟随，且每跳都过闸门。"""
    captured = {}
    _install_fake_client(
        monkeypatch,
        [_FakeResp(302, {"location": "/final.html"}), _FakeResp(text="FINAL")],
        captured,
    )
    monkeypatch.setattr(cf, "_resolve_safe", lambda u: u)
    assert asyncio.run(cf.safe_fetch_url("https://example.com/start")) == "FINAL"
    assert captured["urls"] == [
        "https://example.com/start",
        "https://example.com/final.html",
    ]
