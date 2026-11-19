# push_hub 迁移执行器（137 容器内运行）：按 ; 拆分语句、去 BOM、url 去 +asyncpg
import asyncio
import re
import sys

import asyncpg

MIG = "/tmp/0030_push_hub.sql"


async def main() -> None:
    import os

    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    sql = open(MIG, encoding="utf-8-sig").read()
    # 去掉行内注释后按分号拆分（DDL 无函数体，拆分安全）
    stmts = [s.strip() for s in re.split(r";\s*\n", sql) if s.strip() and not s.strip().startswith("--")]
    ok = 0
    for s in stmts:
        try:
            await conn.execute(s)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"STMT_FAIL: {e}\n  head={s[:80]!r}")
            await conn.close()
            sys.exit(1)
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE tablename LIKE 'push_%' ORDER BY tablename"
    )
    print(f"executed={ok}/{len(stmts)} tables={[r['tablename'] for r in rows]}")
    await conn.close()


asyncio.run(main())
