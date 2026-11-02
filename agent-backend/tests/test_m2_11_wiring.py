"""M2-11 链路接入：解析生成 / 归因输出必须过数值护栏。

纯函数护栏在 ``test_numeric_guard.py`` 已覆盖；这里锁的是**接线**——
护栏没接上，函数再对也没用（这正是「改了提示词但线上仍出旧答案」的同类问题）。
"""
import asyncio

import pytest

from app.core import judge_v2, llm, orchestrator, prompts, tenant_llm


def _run(coro):
    return asyncio.run(coro)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


class _Completions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(self.content)


class _Client:
    def __init__(self, content):
        self.chat = type("C", (), {"completions": _Completions(content)})()


def _cfg():
    return tenant_llm.LLMConfig(
        chat_base_url="http://x", chat_api_key="k", chat_model="fake-model",
        embed_base_url="", embed_api_key="", embed_model="", embed_dim=4,
        model_simple="", model_hard="", rag_top_k=4, source="global",
    )


# ------------------------------------------------------------ 解析生成
def test_solve_output_is_guarded_against_reference(monkeypatch):
    """解析里「答案为 3.04」与可信标答 61/25 冲突 -> 回包前被纠正。"""
    client = _Client('{"answer": "答案为 3.04", "steps": ["代入得 61/25，答案为 3.04"]}')

    async def _fake_resolve(_tenant=None):
        return _cfg()

    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm.tenant_llm, "resolve_llm_config", _fake_resolve)
    monkeypatch.setattr(llm.tenant_llm, "make_client", lambda _cfg: client)

    out = _run(llm.solve(
        "箱中有5个球，E(X)=_____", context={}, mode="explain",
        reference_answer="61/25",
    ))
    assert "3.04" not in out["answer"]
    assert "2.44" in out["answer"]
    assert "3.04" not in out["steps"][0]


def test_solve_without_reference_is_unchanged(monkeypatch):
    client = _Client('{"answer": "答案为 3.04", "steps": []}')

    async def _fake_resolve(_tenant=None):
        return _cfg()

    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm.tenant_llm, "resolve_llm_config", _fake_resolve)
    monkeypatch.setattr(llm.tenant_llm, "make_client", lambda _cfg: client)

    out = _run(llm.solve("题", context={}, mode="explain"))
    assert out["answer"] == "答案为 3.04"


def test_solve_uses_analysis_prompt_from_registry(monkeypatch):
    """提示词必须来自 prompts 注册表（否则 M2-11 的规范只写在没人用的地方）。"""
    captured = {}

    async def _fake_remote(q, c, m, **kwargs):
        captured["messages"] = llm._build_messages(q, c, m)
        return {"answer": "x", "steps": []}

    async def _fake_resolve(_tenant=None):
        return _cfg()

    async def _direct(_call, *a, **kw):
        return await _call("题", {}, "explain")

    monkeypatch.setattr(llm.config, "llm_ready", lambda: True)
    monkeypatch.setattr(llm.tenant_llm, "resolve_llm_config", _fake_resolve)
    monkeypatch.setattr(llm.llm_cache, "cached_solve", _direct)
    monkeypatch.setattr(llm, "_remote_solve", _fake_remote)

    _run(llm.solve("题", context={}, mode="explain"))
    system = captured["messages"][0]["content"]
    assert "不得输出与标准答案冲突的数值" in system
    # 解题提示词是在既有的引导/讲解规则后追加数值规范，不能整体替换（会丢旧规则）
    assert system.endswith(prompts.ANALYSIS_RULES)
    assert "严禁改写题干" in system


# ------------------------------------------------------------ 错因归因
def test_attribution_prompt_is_registry_version():
    assert judge_v2._JUDGE_V2_SYSTEM == prompts.ATTRIBUTION_SYSTEM


def test_attribution_comment_is_guarded(monkeypatch):
    """归因模型在点评里编出与标答冲突的数值 -> 回包前纠正并留痕。"""
    client = _Client(
        '{"correct": false, "first_wrong_step": 2, "error_type": "calc", '
        '"evidence": "3.04", "comment": "正确答案为 3.04，你算错了"}'
    )
    bundle = {
        "type": "解答", "answer": "61/25", "stem": "E(X)=_____",
        "analysis": "", "steps": [
            {"step_no": 1, "step_kind": "derive", "text": "求分布", "explanation": "",
             "error_reason": "", "distractor_map": None, "wrong_value_map": None,
             "typical_errors": None, "label": "", "is_correct": None},
        ],
    }

    async def _fake_load(_h):
        return bundle

    async def _fake_resolve(_tenant=None):
        return _cfg()

    monkeypatch.setattr(judge_v2, "load_bundle", _fake_load)
    monkeypatch.setattr(judge_v2.tenant_llm, "make_client", lambda _cfg: client)
    monkeypatch.setattr(judge_v2.tenant_llm, "resolve_llm_config", _fake_resolve)
    monkeypatch.setattr(judge_v2.config, "llm_ready", lambda: True)

    # 「解答」类要先过过程长度门槛（MIN_PROCESS_LEN=12），所以给一段像样的作答
    out = _run(judge_v2.judge(
        "E(X)=_____", "先求分布，再算期望，我认为答案是 3",
        context={"chunks": [{"content_hash": "h", "score": 0.95}]},
    ))
    assert out["attribution_source"] == "llm"
    assert "3.04" not in out["comment"]
    assert "2.44" in out["comment"]
    assert out["numeric_conflict"] is False
