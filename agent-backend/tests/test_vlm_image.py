"""拍照识题（handle_image）VLM 解析单测：全部 hermetic，不连真实 LLM / VLM / DB。

核心回归点（对应此前"假成功"bug）：
- VLM 未配置（租户无 vlm_model 且无全局 VLM）→ 直接短路返回 vlm_enabled=False，
  绝不把降级文案当题面送进 handle_question；
- VLM 已配置 → 解析出的 base_url/api_key/model 必须原样透传给 ocr.recognize，
  且复用租户 chat 段 api_key。
"""
import asyncio

from app.core import ocr, orchestrator, tenant_llm
from app.core.tenant_llm import LLMConfig


def _cfg(*, vlm_base_url="", vlm_model="", chat_api_key="sk-tenant", chat_base_url="https://c.example/v1"):
    return LLMConfig(
        chat_base_url=chat_base_url,
        chat_api_key=chat_api_key,
        chat_model="qwen-max",
        embed_base_url="",
        embed_api_key="",
        embed_model="",
        embed_dim=1024,
        model_simple="",
        model_hard="",
        rag_top_k=4,
        source="tenant",
        vlm_base_url=vlm_base_url,
        vlm_model=vlm_model,
    )


def test_handle_image_vlm_disabled_short_circuits(monkeypatch):
    async def fake_resolve(tenant_id):
        return _cfg(vlm_base_url="", vlm_model="")  # 未配置

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    called = {"ocr": False}

    async def fake_ocr(*a, **k):
        called["ocr"] = True
        return "x"

    monkeypatch.setattr(ocr, "recognize", fake_ocr)

    out = asyncio.run(orchestrator.handle_image(b"img", tenant_id="t"))
    assert out["vlm_enabled"] is False
    assert out["degraded"] is True
    assert called["ocr"] is False  # 绝不该调 OCR / LLM
    assert "vlm_model" in out["message"] or "视觉模型" in out["message"]


def test_handle_image_vlm_enabled_passes_config_to_ocr(monkeypatch):
    async def fake_resolve(tenant_id):
        return _cfg(vlm_base_url="https://vlm.example/v1", vlm_model="qwen-vl-max",
                    chat_api_key="sk-tenant-secret")

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    captured = {}

    async def fake_ocr(image_bytes, *, base_url, api_key, model):
        captured.update(base_url=base_url, api_key=api_key, model=model)
        return "解一元二次方程 x^2-5x+6=0"

    monkeypatch.setattr(ocr, "recognize", fake_ocr)

    async def fake_hq(text, **kw):
        return {
            "answer": "x=2 或 x=3", "steps": [], "similar": [],
            "knowledge_points": [], "degraded": False,
        }

    monkeypatch.setattr(orchestrator, "handle_question", fake_hq)

    out = asyncio.run(orchestrator.handle_image(b"img", tenant_id="t"))
    assert out["vlm_enabled"] is True
    assert out["degraded"] is False
    assert captured["base_url"] == "https://vlm.example/v1"
    assert captured["model"] == "qwen-vl-max"
    assert captured["api_key"] == "sk-tenant-secret"  # VLM 密钥复用 chat 段
    assert out["recognized_text"] == "解一元二次方程 x^2-5x+6=0"
    assert out["answer"] == "x=2 或 x=3"


def test_handle_image_global_vlm_fallback(monkeypatch):
    # 租户无 vlm_model 时，_global_config 提供全局 VLM 配置
    async def fake_resolve(tenant_id):
        return _cfg(vlm_base_url="https://g-vlm/v1", vlm_model="global-vlm",
                    chat_api_key="sk-global")

    monkeypatch.setattr(tenant_llm, "resolve_llm_config", fake_resolve)
    captured = {}

    async def fake_ocr(image_bytes, *, base_url, api_key, model):
        captured.update(base_url=base_url, api_key=api_key, model=model)
        return "题干"

    monkeypatch.setattr(ocr, "recognize", fake_ocr)
    monkeypatch.setattr(orchestrator, "handle_question",
                        lambda text, **kw: asyncio.sleep(0, result={"answer": "a"}))

    out = asyncio.run(orchestrator.handle_image(b"img", tenant_id="t"))
    assert out["vlm_enabled"] is True
    assert captured["model"] == "global-vlm"
