"""S4 过程采集：/api/app/solve 的提交协议（接口级）。

验收项："空过程提交被正确引导而非误判" —— 前后端都依赖这个契约。
"""
import pytest
from fastapi.testclient import TestClient

from app.core import llm, orchestrator, rag
import main


@pytest.fixture
def client(monkeypatch):
    """起测试客户端：屏蔽启动预热（不连 DB / 不调远程 embedding）。"""
    async def _noop_warm():
        return None

    async def _fake_retrieve(_q, subject=None):
        return {"chunks": [], "points": []}

    async def _fake_solve(_q, context=None, mode="explain", **kwargs):
        # T-D1: 主链路会给 llm.solve 透传 subject/hint_level/content_hash（缓存键用）
        return {"answer": "x=2", "steps": ["移项", "求解"]}

    async def _fake_similar(_q, context=None):
        return []

    monkeypatch.setattr(rag, "warm", _noop_warm)
    monkeypatch.setattr(rag, "retrieve", _fake_retrieve)
    monkeypatch.setattr(llm, "solve", _fake_solve)
    monkeypatch.setattr(llm, "suggest_similar", _fake_similar)

    with TestClient(main.app) as c:
        yield c


def test_empty_process_asks_for_process_not_wrong(client):
    """空作答：返回 need_process=true，且 correct 为 null（不是 false）。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4", "student_answer": ""})
    assert r.status_code == 200
    j = r.json()["judgement"]
    assert j["need_process"] is True
    assert j["correct"] is None          # 关键：None 而不是 False
    assert j["error_type"] is None
    assert "过程" in (j["comment"] or "")


def test_blank_string_process_same_as_empty(client):
    """只输入空白字符等同于没写。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4", "student_answer": "   "})
    assert r.json()["judgement"]["need_process"] is True


def test_with_process_returns_judgement(client):
    """有过程文本时不触发 need_process（未匹配到标注题 -> unknown 走兜底）。"""
    r = client.post(
        "/api/app/solve",
        json={"question": "解方程 2x=4", "student_answer": "因为 2x=4，所以 x=2，过程如下"},
    )
    assert r.status_code == 200
    j = r.json()["judgement"]
    assert j["need_process"] is False


def test_response_exposes_content_hash_for_attribution(client):
    """归因要能归属到题：响应必须带 content_hash（未声明会被 pydantic 过滤掉）。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4"})
    assert "content_hash" in r.json()


def test_without_student_answer_field_means_explain_only(client):
    """不传 student_answer = 只要讲解，不判分（区别于传了空串=交白卷）。"""
    r = client.post("/api/app/solve", json={"question": "解方程 2x=4"})
    assert r.status_code == 200
    assert r.json().get("judgement") is None
