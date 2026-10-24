"""agent-backend 单测公共夹具。

核心约束：**单测不得触碰任何外部依赖**（Redis / PG / 真实 LLM / 真实 embedding）。
T-D1 引入请求级缓存后，`cached_solve` 在未显式传 store 时会按环境挑 Redis/PG 存储，
单测环境里 DB_DSN 指向 106 生产库，一旦连上既污染数据又会因 event loop 复用报错。
故在此统一关闭缓存总开关；需要验证缓存语义的用例显式传 `MemoryStore`。
"""
import pytest

from app.core import config as _config


@pytest.fixture(autouse=True)
def _no_external_deps(monkeypatch):
    """全局关闭缓存与用量总开关，确保任何用例都不会去连 Redis/PG。

    T-D3 的 ``record_usage`` 会写 106 的 ``llm_usage``、``budget_state`` 会查
    Redis——单测环境 DB_DSN 同样指向 106，故一并关掉（需要验证用量语义的用例
    显式打开开关并注入 ``recorder``）。
    """
    monkeypatch.setattr(_config, "CACHE_ENABLED", False)
    monkeypatch.setattr(_config, "USAGE_ENABLED", False)
    yield
