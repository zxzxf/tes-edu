"""T-A3 RAG 读取侧固化：pgvector 召回 SQL 契约 + 返回结构 + dev 回退 + healthz 探活。

背景：读取侧（rag._search_pg）已连 106 course_kb_embeddings，真实库自召回评测
数学 123/123、物理 90/90 TOP1=100%（2026-10-24，容器内 eval_retrieval_realbank.py）。
本文件把该链路的**契约**钉进单测，防止后续改动破坏：
- SQL 必须带 is_demo 隔离谓词与 subject 过滤
- limit 必须等于 TOP_K
- 向量字面量维度必须与 EMBED_DIM 一致（与 ws qwen3.7-text-embedding 1024 对齐）
- content_hash 必须透传（judge v2 步骤级归因靠它定位 course_kb_steps）
- dev 模式（db_ready=False）仍走内存库，不抛异常
"""
import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config, embeddings, rag  # noqa: E402
from app.db import pg  # noqa: E402
import main  # noqa: E402


# ------------------------------------------------------------------ 假 pg

class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._rows

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return 1


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        outer = self

        class _Ctx:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _patch_pg(monkeypatch, rows):
    """让 rag._search_pg 与 healthz 探活都拿到假连接池，返回 conn 供断言。"""
    conn = _FakeConn(rows)
    monkeypatch.setattr(config, "db_ready", lambda: True)

    async def _get_pool():
        return _FakePool(conn)

    monkeypatch.setattr(pg, "get_pool", _get_pool)
    return conn


@pytest.fixture
def fake_embed(monkeypatch):
    """屏蔽真实 embedding 调用，返回固定维度向量。"""
    async def _embed_one(_text, *, embed_cfg=None):
        return [0.1] * config.EMBED_DIM

    monkeypatch.setattr(embeddings, "embed_one", _embed_one)


_ROWS = [
    {
        "content": "解方程 2x=4",
        "knowledge_point": "MA-B1-C1-S3-K02",
        "difficulty": "easy",
        "answer": "x=2",
        "content_hash": "hash-001",
        "score": 0.91,
    },
    {
        "content": "另一道同类题",
        "knowledge_point": "MA-B1-C1-S3-K02",
        "difficulty": "medium",
        "answer": "x=3",
        "content_hash": None,  # 库里允许空 hash，必须透传为空串而非 None 泄漏
        "score": 0.77,
    },
]


# ------------------------------------------------------------------ pg 模式契约

def test_search_pg_sql_contract(fake_embed, monkeypatch):
    """SQL 必须：隔离 seed demo 假题、支持 subject 过滤、limit=TOP_K、维度=EMBED_DIM。"""
    conn = _patch_pg(monkeypatch, _ROWS)
    asyncio.run(rag.retrieve("解方程 2x=4", subject="数学"))

    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert "course_kb_embeddings" in sql
    assert "is_demo IS NOT TRUE" in sql          # demo 假题硬隔离（历史决策）
    assert "$2::text IS NULL OR subject = $2" in sql
    assert args[1] == "数学"                       # subject 透传为 $2
    assert args[2] == config.TOP_K                 # limit 取配置
    vec = args[0]
    assert vec.startswith("[") and vec.endswith("]")
    assert len(vec.strip("[]").split(",")) == config.EMBED_DIM  # 1024 对齐


def test_search_pg_passes_through_hash_and_dedups_points(fake_embed, monkeypatch):
    """返回结构：content_hash 透传（None→''）；points 去重且保序。"""
    _patch_pg(monkeypatch, _ROWS)
    out = asyncio.run(rag.retrieve("解方程 2x=4", subject="数学"))
    chunks = out["chunks"]
    assert len(chunks) == 2
    assert chunks[0]["content_hash"] == "hash-001"
    assert chunks[1]["content_hash"] == ""          # None 不得泄漏给消费端
    assert chunks[0]["knowledge_point"] == "MA-B1-C1-S3-K02"
    assert out["points"] == ["MA-B1-C1-S3-K02"]     # 两个 chunk 同点 → 去重为一个


def test_retrieve_subject_none_means_all(fake_embed, monkeypatch):
    """subject 不传 = 全库检索（$2 为 NULL，SQL 分支放过）。"""
    conn = _patch_pg(monkeypatch, _ROWS)
    asyncio.run(rag.retrieve("任意问题"))
    _sql, args = conn.calls[0]
    assert args[1] is None


# ------------------------------------------------------------------ dev 回退

def test_dev_mode_falls_back_to_memory(fake_embed, monkeypatch):
    """db_ready=False 时走内存库，链路不挂（本地/CI 无 106 时保证可跑）。"""
    monkeypatch.setattr(config, "db_ready", lambda: False)
    monkeypatch.setattr(rag, "_LOADED", True)  # 挡住 _load_seed 用种子文件覆盖 _DOCS
    monkeypatch.setattr(rag, "_DOCS", [
        {"content": "解方程 2x=4", "knowledge_point": "KP-1", "subject": "数学"},
        {"content": "无关内容", "knowledge_point": "KP-2", "subject": "物理"},
    ])
    monkeypatch.setattr(rag, "_VEC", [[1.0, 0.0], [0.0, 1.0]])
    monkeypatch.setattr(rag, "_VEC_READY", True)
    out = asyncio.run(rag.retrieve("解方程", subject="数学"))
    assert out["points"] == ["KP-1"]               # subject 过滤生效
    assert out["chunks"][0]["content_hash"] == ""  # dev 文档无 hash，键仍存在


# ------------------------------------------------------------------ healthz 探活

def test_healthz_pings_db_when_configured(monkeypatch):
    """db_ping：配置了就真探活；连通=true，挂了=false（不 500）。"""
    _patch_pg(monkeypatch, [])

    async def _noop_warm():
        return None

    monkeypatch.setattr(rag, "warm", _noop_warm)
    with TestClient(main.app) as c:
        j = c.get("/healthz").json()
    assert j["db"] is True
    assert j["db_ping"] is True


def test_healthz_db_ping_false_when_unreachable(monkeypatch):
    """pgvector 挂了：db_ping=false 立即可见（status 仍 ok，liveness 与 readiness 分离）。"""
    _patch_pg(monkeypatch, [])

    async def _boom():
        raise OSError("connection refused")

    monkeypatch.setattr(pg, "get_pool", _boom)

    async def _noop_warm():
        return None

    monkeypatch.setattr(rag, "warm", _noop_warm)
    with TestClient(main.app) as c:
        j = c.get("/healthz").json()
    assert j["db"] is True
    assert j["db_ping"] is False
    assert j["status"] == "ok"


def test_healthz_db_ping_none_when_not_configured(monkeypatch):
    """未配置 DB_DSN（dev）：db_ping=None，不误导为故障。"""
    monkeypatch.setattr(config, "db_ready", lambda: False)

    async def _noop_warm():
        return None

    monkeypatch.setattr(rag, "warm", _noop_warm)
    with TestClient(main.app) as c:
        j = c.get("/healthz").json()
    assert j["db"] is False
    assert j["db_ping"] is None
