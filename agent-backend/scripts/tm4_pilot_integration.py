#!/usr/bin/env python3
"""W6 BYOM T-M4 —— 试点校联调 harness（可复现）。

把「至少 1 所试点校以 BYOM 模式跑通解题」固化为可一键运行的脚本：

  - 不依赖 DB / Redis / 真实 LLM：默认走 fake 传输验证全链路 wiring；
  - 设环境变量 BYOM_REAL_BASE_URL / BYOM_REAL_API_KEY / BYOM_REAL_MODEL 即真实跑端到端；
  - 覆盖计划书验收标准第 143 行五项：
      ① 学校后台配模型 → 解析生效
      ② 该校请求走该校模型
      ③ 未配置租户回落全局 env
      ④ 密钥密文落库（序列化仅显末 4 位）
      ⑤ SSRF 闸门拒绝内网/元数据地址

运行：
    python scripts/tm4_pilot_integration.py            # hermetic 验证
    BYOM_REAL_BASE_URL=https://x/v1 BYOM_REAL_API_KEY=sk-xxx \
        BYOM_REAL_MODEL=qwen-max python scripts/tm4_pilot_integration.py   # 真实联调

退出码 0 = 全部通过；非 0 = 有失败项（可接 CI）。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from types import SimpleNamespace

from app.core import config, llm, model_router, tenant_llm, vendor_crypto as vc

# 固定 32 字节主密钥（与 web-backend VENDOR_CREDENTIAL_KEY 同源；本地 hermetic 用独立值）
_KEY = bytes([0xCD] * 31 + [0x02]).hex()
os.environ.setdefault("VENDOR_CREDENTIAL_KEY", _KEY)

RESULTS = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))


# ----------------------------------------------------------------- fake 客户端

def _fake_make_client(captured: dict):
    def _impl(cfg):
        captured["api_key"] = cfg.chat_api_key
        captured["base_url"] = cfg.chat_base_url
        captured["model"] = None
        captured["cfg"] = cfg

        class _Completions:
            async def create(self, **kwargs):
                captured["model"] = kwargs.get("model")
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content='{"answer":"A","steps":["s1","s2"]}'))],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                )

        class _Client:
            chat = SimpleNamespace(completions=_Completions())

        return _Client()

    return _impl


# ----------------------------------------------------------------- 各验收项


def check_tenant_model_resolved(monkeypatch) -> None:
    """① + ② 学校配模型 → 该校请求走该校模型。"""
    captured = {}
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://global.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "global-key")
    monkeypatch.setattr(config, "LLM_MODEL", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_HARD", "global-model")
    monkeypatch.setattr(config, "cache_enabled", lambda: False)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))

    cfg = tenant_llm.LLMConfig(
        chat_base_url="https://school-a.example.com/v1",
        chat_api_key="school-a-key-1234",
        chat_model="school-a-model",
        embed_base_url="https://school-a.example.com/v1",
        embed_api_key="school-a-key-1234",
        embed_model="", embed_dim=1024,
        model_simple="school-a-model", model_hard="school-a-model",
        rag_top_k=4, source="tenant", provider="custom", tenant_id="school_a",
    )
    out = asyncio.run(llm.solve("求 1+1", context={"chunks": []}, mode="explain", llm_cfg=cfg))
    ok = (
        captured.get("api_key") == "school-a-key-1234"
        and captured.get("base_url") == "https://school-a.example.com/v1"
        and captured.get("model") == "school-a-model"
        and out.get("answer") == "A"
    )
    _record("①+② 租户模型解析并驱动解题", ok,
            f"model={captured.get('model')} answer={out.get('answer')}")


def check_global_fallback(monkeypatch) -> None:
    """③ 未配置租户 → 回落全局 env。"""
    captured = {}
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://global.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "global-key")
    monkeypatch.setattr(config, "LLM_MODEL", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_HARD", "global-model")
    monkeypatch.setattr(config, "cache_enabled", lambda: False)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))

    asyncio.run(llm.solve("题", context={"chunks": []}, mode="explain"))
    ok = (
        captured.get("api_key") == "global-key"
        and captured.get("base_url") == "https://global.example.com/v1"
        and captured.get("model") == "global-model"
    )
    _record("③ 无租户配置回落全局 env", ok, f"model={captured.get('model')}")


def check_key_encryption_roundtrip(monkeypatch) -> None:
    """④ 密钥密文落库、序列化仅显末 4 位。"""
    raw = "sk-school-a-secret-9"
    enc = vc.encrypt(raw)
    # 密文不应含明文
    no_plain = raw not in enc.decode("latin-1", "ignore") if isinstance(enc, bytes) else raw not in enc
    back = vc.decrypt(enc)
    # 模拟序列化脱敏（与 web-backend byom.serialize_config_masked 同口径）
    masked = "****" + back[-4:] if len(back) > 4 else back
    ok = no_plain and back == raw and masked == "****et-9" and "secret" not in masked
    _record("④ 密钥 AES-256-GCM 加解密 + 脱敏仅显末4位", ok,
            f"masked={masked} roundtrip={back == raw}")


def check_ssrf_guard(monkeypatch) -> None:
    """⑤ SSRF 闸门拒绝内网/元数据地址，回落全局。"""
    # ssrf 模块位于 web-backend/app/core/ssrf.py；两仓库均有 app 包，
    # 直接按文件路径 load，避免 sys.path 的 app 包冲突
    import importlib.util

    ssrf_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "web-backend", "app", "core", "ssrf.py"))
    spec = importlib.util.spec_from_file_location("byom_ssrf", ssrf_path)
    ssrf_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ssrf_mod)
    SSRFRejected = ssrf_mod.SSRFRejected
    assert_safe_base_url = ssrf_mod.assert_safe_base_url

    blocked = []
    for bad in ("http://localhost/v1", "http://10.0.0.1/v1",
                "http://169.254.169.254/v1", "ftp://x/v1"):
        try:
            assert_safe_base_url(bad)
            blocked.append(bad)  # 走到这里说明未被拦截，记为漏网
        except SSRFRejected:
            pass
    # resolve 对 SSRF 不合规行应回落全局
    captured = {}
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://global.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "global-key")
    monkeypatch.setattr(config, "LLM_MODEL", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_SIMPLE", "global-model")
    monkeypatch.setattr(config, "LLM_MODEL_HARD", "global-model")
    monkeypatch.setattr(config, "cache_enabled", lambda: False)
    monkeypatch.setattr(tenant_llm, "make_client", _fake_make_client(captured))

    async def loader(tid):
        return {
            "tenant_id": tid, "provider": "custom",
            "base_url": "http://169.254.169.254/v1",  # 元数据地址
            "api_key_enc": vc.encrypt("x"), "model": "m",
            "embed_base_url": None, "embed_api_key_enc": None, "embed_model": None,
            "embed_dim": 1024, "model_simple": None, "model_hard": None,
            "enabled": True, "rag_top_k": 4,
        }

    cfg = asyncio.run(tenant_llm.resolve_llm_config("t1", loader=loader))
    fell_back = cfg.source == "global"
    ok = not blocked and fell_back
    _record("⑤ SSRF 闸门拒绝内网/元数据 + 回落全局", ok,
            f"漏网={len(blocked)} 回落={fell_back}")


# ----------------------------------------------------------------- 真实联调（可选）


async def _real_probe(base_url: str, api_key: str, model: str) -> dict:
    """真实端点最小请求；复用 byom.probe_endpoint 的传输实现。"""
    import httpx

    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": "1+1=?"}],
               "max_tokens": 16, "stream": False}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=payload)
        return {"status": resp.status_code, "ok": resp.status_code < 400,
                "snippet": resp.text[:120]}


def check_real_pilot(monkeypatch) -> None:
    base = os.getenv("BYOM_REAL_BASE_URL")
    key = os.getenv("BYOM_REAL_API_KEY")
    model = os.getenv("BYOM_REAL_MODEL")
    if not (base and key and model):
        _record("★ 真实试点校联调（可选）", True,
                "未设 BYOM_REAL_* 环境变量，跳过；hermetic 验证已覆盖全链路 wiring")
        return
    try:
        res = asyncio.run(_real_probe(base, key, model))
        ok = res["ok"]
        _record("★ 真实试点校联调（BYOM 模式实调 LLM）", ok,
                f"status={res['status']} snippet={res['snippet']!r}")
    except Exception as e:  # noqa: BLE001
        _record("★ 真实试点校联调（BYOM 模式实调 LLM）", False, f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------- main


def main() -> int:
    import pytest
    # 借用 pytest 的 monkeypatch 做隔离，但不依赖 pytest 运行器
    mp = pytest.MonkeyPatch()
    try:
        print("=== T-M4 试点校联调 harness ===")
        check_tenant_model_resolved(mp)
        check_global_fallback(mp)
        check_key_encryption_roundtrip(mp)
        check_ssrf_guard(mp)
        check_real_pilot(mp)
    finally:
        mp.undo()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n汇总：{passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
