"""RAG 检索降级：向量化失败不得炸穿解题链路。

2026-10-27 线上事故：租户 embed 端点 429（workspace 预算耗尽），
`embeddings.embed_one` 抛异常 → `rag.retrieve` 未捕获 → `/api/app/solve`
与 `/api/app/solve_image` 直接 500，学生端整条链路不可用。

RAG 是增强项：检索不到知识点也该能解题（只是质量下降），绝不该 500。
"""
import asyncio

from app.core import rag


class _Cfg:
    """模拟租户 embed 配置（供 embed_one 判断是否按租户路由）。"""

    embed_model = "text-embedding-v3"
    embed_base_url = "https://example.invalid/v1"
    embed_api_key = "sk-x"
    embed_dim = 1024


def test_retrieve_degrades_when_embed_fails(monkeypatch):
    """embed 抛异常 → 返回空上下文 + degraded=True，不抛。"""
    calls = []

    async def boom(text, embed_cfg=None):
        calls.append(embed_cfg)
        raise RuntimeError("429 budget limit reached")

    monkeypatch.setattr(rag.embeddings, "embed_one", boom)

    ctx = asyncio.run(rag.retrieve("求顶点坐标", subject="数学", llm_cfg=None))
    assert ctx["chunks"] == []
    assert ctx["points"] == []
    assert ctx["degraded"] is True
    assert len(calls) == 1


def test_retrieve_falls_back_to_global_then_degrades(monkeypatch):
    """租户配置失败后先试全局；全局也失败才降级（不放弃任何一次机会）。"""
    seen = []

    async def flaky(text, embed_cfg=None):
        seen.append("tenant" if embed_cfg is not None else "global")
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(rag.embeddings, "embed_one", flaky)

    ctx = asyncio.run(rag.retrieve("求顶点坐标", llm_cfg=_Cfg()))
    assert seen == ["tenant", "global"]  # 先租户后全局
    assert ctx["degraded"] is True


def test_retrieve_succeeds_via_global_fallback(monkeypatch):
    """租户端点挂了但全局可用 → 正常检索，不算降级。"""

    async def flaky(text, embed_cfg=None):
        if embed_cfg is not None:
            raise RuntimeError("tenant endpoint 429")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(rag.embeddings, "embed_one", flaky)
    monkeypatch.setattr(rag.config, "db_ready", lambda: False)
    # dev 内存路径会走 _ensure_vectors -> embeddings.embed（真实远程调用），也要打桩
    monkeypatch.setattr(
        rag.embeddings, "embed", lambda texts, embed_cfg=None: _coro([[0.1] * 4 for _ in texts])
    )
    monkeypatch.setattr(rag, "_ensure_vectors", lambda: _coro(None))

    ctx = asyncio.run(rag.retrieve("求顶点坐标", llm_cfg=_Cfg()))
    assert ctx["degraded"] is False  # 全局兜住了
    assert isinstance(ctx["chunks"], list)


def test_pg_search_failure_also_degrades(monkeypatch):
    """向量化成功但 PG 检索挂了，同样降级而不是 500。"""
    monkeypatch.setattr(
        rag.embeddings, "embed_one", lambda t, embed_cfg=None: _coro([0.1] * 4)
    )

    async def pg_boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(rag.config, "db_ready", lambda: True)
    monkeypatch.setattr(rag, "_search_pg", pg_boom)

    ctx = asyncio.run(rag.retrieve("求顶点坐标"))
    assert ctx["chunks"] == []
    assert ctx["degraded"] is True


async def _coro(v):
    return v
