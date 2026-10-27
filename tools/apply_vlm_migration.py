"""在 137 的 agent 容器内执行 VLM 迁移：给 tenant_model_config 增加 vlm_base_url / vlm_model 两列。

用法（在本地执行）：
    ssh 137 'docker exec -i -w /app edu-agent-backend python -' < apply_vlm_migration.py

复用容器内已配置的 DB_DSN（指向 106 共享库），幂等（IF NOT EXISTS）。
"""
import asyncio

from app.db.pg import get_pool

SQL = """
ALTER TABLE tenant_model_config
    ADD COLUMN IF NOT EXISTS vlm_base_url TEXT;
ALTER TABLE tenant_model_config
    ADD COLUMN IF NOT EXISTS vlm_model VARCHAR(128);
"""


async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(SQL)
        cols = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'tenant_model_config' AND column_name LIKE 'vlm%' "
            "ORDER BY column_name"
        )
        print("tenant_model_config vlm columns after migration:")
        for r in cols:
            print(f"  - {r['column_name']} ({r['data_type']})")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
