"""BYOM T-M1: default 租户占位种子。

插入哨兵行 `__global__`（enabled=False, api_key_enc=加密空串），用于显式标记
"回落全局 env"。不存在即幂等：ON CONFLICT (tenant_id) DO NOTHING。

未配置任何 tenant_model_config 行的租户本就走全局 env（见 T-M2 fallback），
此占位行仅作"显式声明"用途，可选执行。

用法：
    python migrations/seed_default_tenant.py
（依赖 DATABASE_URL 与 VENDOR_CREDENTIAL_KEY 环境变量，与运行时一致）
"""
import asyncio
import os
import sys

# 让脚本能 import app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.course_kb import Base  # noqa: F401 确保元数据加载
from app.models import byom  # noqa: F401 注册表
from app.services import vendor_crypto as vc

SENTINEL_TENANT = "__global__"


async def seed():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL 未设置")
    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        enc = vc.encrypt("")  # 加密空串，证明密文落库、明文不落
        await db.execute(
            text(
                """
                INSERT INTO tenant_model_config
                    (tenant_id, provider, base_url, api_key_enc, model, enabled)
                VALUES (:tid, 'custom', '', :enc, '', FALSE)
                ON CONFLICT (tenant_id) DO NOTHING
                """
            ),
            {"tid": SENTINEL_TENANT, "enc": enc},
        )
        await db.commit()
        print(f"seeded sentinel tenant {SENTINEL_TENANT} (idempotent)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
