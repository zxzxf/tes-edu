"""W6 BYOM T-M4 hermetic 集成单测（无 openai / 无 pytest-asyncio / 无 DB）。

验证 T-M4 接入成果：agent 解题链路真正读取并按租户模型配置调用 LLM。

- solve / judge / similar / intent / socratic 的 chat 调用均经 tenant_llm.make_client 构造，
  用租户 chat_base_url / chat_api_key / chat_model（及 model_simple/model_hard 分级）；
- 无租户配置时回落全局 env，行为与接入 BYOM 前一致；
- resolve_llm_config 对「租户行 base_url 不合规(SSRF)」回落全局、对合法行正确解密；
- model_router 接受租户 simple/hard 覆盖。

驱动方式：async 协程用 asyncio.run 包裹（与 test_degrade_usage 同风格），
make_client 被替换成记录 cfg 的假客户端，全程不 import openai。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from types import SimpleNamespace

import pytest

from app.core import config, llm, model_router, tenant_llm, usage, vendor_crypto as vc

# 固定 32 字节密钥（hex）供加密租户 api_key
_KEY = bytes([0xCD] * 31 + [0x02]).hex()


def _fake_make_client(captured: dict):
    """替换 tenant_llm.make_client：记录被解析出的 LLMConfig，返回假客户端。"""

    def _impl(cfg):
        captured["api_key"] = cfg.chat_api_key
        captured["base_url"] = cfg.chat_base_url
        captured["cfg"] = cfg
        return _FakeClient(captured)

    return _impl


class _FakeCompletions:
    def __init__(self, captured: dict):
        self._cap = captured

    async def create(self, **kwargs):
        self._cap["model"] = kwargs.get("model")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"A","steps":["s1"]}'))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )


class _FakeClient:
    def __init__(self, captured: dict):
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


def _basic_env(monkeypatch):
    """打开远程开关 + 关缓存 + 桩掉用量/预算，避免触外部依赖。"""
    # 全局 env 三件套需一致：LLM_MODEL_SIMPLE/HARD 在 import 时即冻结为默认值，
    # 若只 patch LLM_MODEL，全局回落的 model_simple/hard 仍是 import 期默认值，
    # 会导致 route_model 走「single=simple」分支回落到冻结值而非 LLM_MODEL。
    # 生产环境 env 三者一致，此处同样保持三者一致。
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://fake/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "global-key")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3.8-flash")
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", "qwen3.8-flash")
    monkeypatch.setattr(config, "LLM_MODEL_HARD", "qwen3.8-flash")
    monkeypatch.setattr(config, "cache_enabled", lambda: False)
    collected = []
    monkeypatch.setattr(usage, "record_usage_bg", lambda **k: collected.append(k))
    monkeypatch.setattr(usage, "budget_state", lambda: {})
    return collected


def _tenant_cfg(**over):
    base = dict(
        chat_base_url="https://tenant.example.com/v1",
        chat_api_key="tenant-key-1234",
        chat_model="tenant-model-x",
        embed_base_url="https://tenant.example.com/v1",
        embed_api_key="tenant-key-1234",
        embed_model="",
        embed_dim=1024,
        model_simple="tenant-model-x",
        model_hard="tenant-model-x",
        rag_top_k=4,
        source="tenant",
        provider="custom",
    )
    base.update(over)
    return tenant_llm.LLMConfig(**base)


# ------------------------------------------------------------------ solve 用租户配置

def test_solve_uses_tenant_llm_config(monkeypatch):
    captured = {}
    collected = _basic_env(monkeypatch)
    monkeypatch.setattr(usage, "record_usage_bg", lambda **k: collected.append(k))
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))
    cfg = _tenant_cfg()

    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="explain", llm_cfg=cfg))

    assert captured["api_key"] == "tenant-key-1234"
    assert captured["base_url"] == "https://tenant.example.com/v1"
    assert captured["model"] == "tenant-model-x"
    assert out["answer"] == "A"
    # 用量记录也应反映租户模型
    assert collected and collected[-1].get("model") == "tenant-model-x"


# ------------------------------------------------------------------ 租户分级路由

def test_solve_routes_tenant_hard_for_multi_step_explain(monkeypatch):
    captured = {}
    _basic_env(monkeypatch)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))
    # 租户显式分级：小模型 s / 大模型 h
    cfg = _tenant_cfg(model_simple="t-small", model_hard="t-big", chat_model="t-small")

    asyncio.run(
        llm.solve("题", context={"chunks": [{"text": "x", "knowledge_point": "y"}]},
                  mode="explain", steps=5, llm_cfg=cfg)
    )
    # explain + steps>=3 -> hard
    assert captured["model"] == "t-big"


def test_solve_routes_tenant_simple_for_quick(monkeypatch):
    captured = {}
    _basic_env(monkeypatch)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))
    cfg = _tenant_cfg(model_simple="t-small", model_hard="t-big", chat_model="t-small")

    asyncio.run(llm.solve("题", context={"chunks": []}, mode="quick", llm_cfg=cfg))
    assert captured["model"] == "t-small"


# ------------------------------------------------------------------ 全局回落

def test_solve_falls_back_to_global_without_tenant(monkeypatch):
    captured = {}
    _basic_env(monkeypatch)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))

    # 不传 llm_cfg / tenant_id -> 回落全局 env
    asyncio.run(llm.solve("题", context={"chunks": []}, mode="explain"))
    assert captured["api_key"] == "global-key"
    assert captured["base_url"] == "https://fake/v1"
    assert captured["model"] == "qwen3.8-flash"


# ------------------------------------------------------------------ resolve_llm_config 单元

def test_resolve_falls_back_when_tenant_url_fails_ssrf(monkeypatch):
    os.environ["VENDOR_CREDENTIAL_KEY"] = _KEY
    captured = {}

    async def loader(tid):
        return {
            "tenant_id": tid, "provider": "custom",
            "base_url": "http://evil/v1",  # 非 https -> SSRF 拦截
            "api_key_enc": vc.encrypt("secret"), "model": "m",
            "embed_base_url": None, "embed_api_key_enc": None, "embed_model": None,
            "embed_dim": 1024, "model_simple": None, "model_hard": None,
            "enabled": True, "rag_top_k": 4,
        }

    cfg = asyncio.run(tenant_llm.resolve_llm_config("t1", loader=loader))
    assert cfg.source == "global"


def test_resolve_tenant_row_decrypts(monkeypatch):
    os.environ["VENDOR_CREDENTIAL_KEY"] = _KEY

    async def loader(tid):
        return {
            "tenant_id": tid, "provider": "aliyun",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_enc": vc.encrypt("tenant-secret-9"), "model": "qwen-max",
            "embed_base_url": None, "embed_api_key_enc": None, "embed_model": None,
            "embed_dim": 1024, "model_simple": "qwen-plus", "model_hard": "qwen-max",
            "enabled": True, "rag_top_k": 4,
        }

    cfg = asyncio.run(tenant_llm.resolve_llm_config("t2", loader=loader))
    assert cfg.source == "tenant"
    assert cfg.chat_api_key == "tenant-secret-9"
    assert cfg.chat_model == "qwen-max"
    assert cfg.model_hard == "qwen-max"
    assert cfg.model_simple == "qwen-plus"


# ------------------------------------------------------------------ model_router 租户覆盖

def test_model_router_uses_tenant_overrides():
    assert model_router.route_model(mode="explain", steps=5, simple="s", hard="h")["model"] == "h"
    assert model_router.route_model(mode="quick", simple="s", hard="h")["model"] == "s"
    # 不传覆盖 -> 全局单模型
    r = model_router.route_model(mode="explain", steps=5)
    assert r["grade"] == "single" and r["model"] == config.LLM_MODEL
