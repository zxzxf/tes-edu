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
    """全局关闭缓存总开关，确保任何用例都不会去连 Redis/PG。"""
    monkeypatch.setattr(_config, "CACHE_ENABLED", False)
    yield
