"""W6 BYOM T-M3 hermetic 单测（sqlite 内存库 + 假 transport，不连生产库/外部服务）。

覆盖：
- 预设清单种子幂等（seed_presets 仅插缺失行）；list_presets 排序 + 模型解析
- SSRF 闸门拒绝 http / localhost / 私网 IP / 云元数据；放行公开 https
- serialize_config_masked 脱敏（仅末4位、明文不回显、has_api_key 布尔）
- upsert_config 密钥保留语义（编辑留空不覆盖、embed 显式 "" 清空）
- probe_endpoint 经注入 _transport 的连通性自检（SSRF 兜底拦截 + 假响应 ok 判定）

为了可在未安装 pytest-asyncio 的环境下运行，异步逻辑统一用 asyncio.run 驱动
（与仓库其余 @pytest.mark.asyncio 用例互不冲突，可独立执行）。

运行：pytest tests/test_byom_tm3.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.ssrf import SSRFRejected, assert_safe_base_url
from app.models import byom as _byom_mod  # noqa: F401 注册 tenant_model_config
from app.models import byom_preset as _byom_preset_mod  # noqa: F401 注册 vendor_preset
from app.models.course_kb import Base
from app.services import byom as byom_svc
from app.services import byom_preset as preset_svc

# 固定 32 字节密钥（hex 表示）供加密/解密，避免依赖环境派生
_TEST_KEY = bytes([0xAB] * 31 + [0x01]).hex()


async def _make_session():
    """在当前事件循环内建好内存库并回传 session_factory（避免跨 loop 绑定问题）。"""
    os.environ.setdefault("VENDOR_CREDENTIAL_KEY", _TEST_KEY)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return factory


def _run(coro):
    """用独立的事件循环执行一个协程（不依赖 pytest-asyncio）。"""
    return asyncio.run(coro)


# ---------- 预设清单：种子幂等 ----------

def test_seed_presets_idempotent():
    async def _run_async():
        factory = await _make_session()
        async with factory() as s:
            first = await preset_svc.seed_presets(s)
            assert first == 9, f"首轮应插入 9 个预设，实际 {first}"
            second = await preset_svc.seed_presets(s)
            assert second == 0, "二次种子应幂等（0 新增）"
            cnt = (await s.execute(select(func.count()).select_from(_byom_preset_mod.VendorPreset))).scalar()
            assert cnt == 9

    _run(_run_async())


def test_list_presets_sorted_and_parsed():
    async def _run_async():
        factory = await _make_session()
        async with factory() as s:
            await preset_svc.seed_presets(s)
            presets = await preset_svc.list_presets(s)
            assert len(presets) == 9
            # 按 sort_order 升序：第一个应为阿里云（sort 1），custom 在最后（99）
            assert presets[0]["provider"] == "aliyun"
            assert presets[-1]["provider"] == "custom"
            aliyun = next(p for p in presets if p["provider"] == "aliyun")
            assert isinstance(aliyun["models"], list) and len(aliyun["models"]) >= 1
            assert aliyun["base_url"].startswith("https://")

    _run(_run_async())


# ---------- SSRF 闸门 ----------

def test_ssrf_rejects_non_https():
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("http://api.example.com/v1")
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("ftp://x")


def test_ssrf_rejects_localhost_and_private():
    for bad in [
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://0.0.0.0/v1",
        "https://10.0.0.5/v1",
        "https://172.16.5.5/v1",
        "https://192.168.1.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/v1",
    ]:
        with pytest.raises(SSRFRejected):
            assert_safe_base_url(bad)


def test_ssrf_allows_public_https():
    assert assert_safe_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert assert_safe_base_url("https://api.deepseek.com/v1")


def test_ssrf_whitelist_restricts(monkeypatch):
    monkeypatch.setenv("BYOM_ALLOWED_DOMAINS", "*.aliyuncs.com,openai.com")
    assert assert_safe_base_url("https://dashscope.aliyuncs.com/v1")
    with pytest.raises(SSRFRejected):
        assert_safe_base_url("https://api.deepseek.com/v1")
    monkeypatch.delenv("BYOM_ALLOWED_DOMAINS", raising=False)


# ---------- 脱敏序列化 ----------

def test_serialize_config_masked():
    async def _run_async():
        factory = await _make_session()
        async with factory() as s:
            await byom_svc.upsert_config(
                s,
                tenant_id="t_msk",
                provider="aliyun",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="qwen-max",
                api_key_plain="sk-secret-abcd",
                embed_api_key_plain="sk-embed-wxyz",
            )
            obj = await byom_svc.get_config(s, "t_msk")
            out = byom_svc.serialize_config_masked(obj)
            # 明文绝不回显，仅末 4 位
            assert "sk-secret-abcd" not in str(out)
            assert "sk-embed-wxyz" not in str(out)
            assert out["api_key_masked"] == "****abcd"
            assert out["embed_api_key_masked"] == "****wxyz"
            assert out["has_api_key"] is True and out["has_embed_api_key"] is True
            assert out["provider"] == "aliyun" and out["model"] == "qwen-max"

    _run(_run_async())


# ---------- 密钥保留语义 ----------

def test_upsert_keeps_key_when_blank():
    async def _run_async():
        factory = await _make_session()
        async with factory() as s:
            await byom_svc.upsert_config(
                s,
                tenant_id="t_keep",
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                api_key_plain="sk-original-1111",
                embed_api_key_plain="sk-emb-2222",
            )
            # 编辑：api_key_plain 留空 → 不覆盖；embed_api_key_plain="" → 显式清空
            await byom_svc.upsert_config(
                s,
                tenant_id="t_keep",
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                api_key_plain=None,
                embed_api_key_plain="",
            )
            obj = await byom_svc.get_config(s, "t_keep")
            assert byom_svc.decrypt_api_key(obj.api_key_enc) == "sk-original-1111", "原 chat key 应保留"
            assert obj.embed_api_key_enc is None, "embed key 显式清空应为 NULL"

    _run(_run_async())


# ---------- 连通性自检（假 transport，hermetic） ----------

class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


def test_probe_ok_and_status_codes():
    async def _run_async():
        async def fake_200(base_url, api_key, model, timeout):
            return _FakeResp(200)

        res = await byom_svc.probe_endpoint(
            "https://api.deepseek.com/v1", "sk-x", "deepseek-chat", _transport=fake_200
        )
        assert res["ok"] is True and res["status_code"] == 200

        async def fake_401(base_url, api_key, model, timeout):
            return _FakeResp(401)

        res401 = await byom_svc.probe_endpoint(
            "https://api.deepseek.com/v1", "sk-x", "deepseek-chat", _transport=fake_401
        )
        assert res401["ok"] is False and res401["status_code"] == 401
        assert "401" in res401["detail"]

    _run(_run_async())


def test_probe_ssrf_blocked_without_network():
    async def _run_async():
        # 私网地址不应触达网络，直接被 SSRF 闸门拦截（ok=false）
        def should_not_call(*a, **k):
            raise RuntimeError("transport 不应被调用")

        res = await byom_svc.probe_endpoint(
            "https://127.0.0.1/v1", "sk-x", "m", _transport=should_not_call
        )
        assert res["ok"] is False
        assert "SSRF" in res["detail"]

    _run(_run_async())


def test_probe_network_error_is_ok_false():
    async def _run_async():
        async def boom(base_url, api_key, model, timeout):
            raise ConnectionError("refused")

        res = await byom_svc.probe_endpoint(
            "https://api.deepseek.com/v1", "sk-x", "m", _transport=boom
        )
        assert res["ok"] is False and res["status_code"] is None

    _run(_run_async())
