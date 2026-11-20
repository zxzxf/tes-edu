# 通用迁移执行器（137 容器内运行）：python run_migration.py /tmp/00NN_xxx.sql [表前缀]
# 按 ; 拆分语句、去 BOM、url 去 +asyncpg；末尾按前缀回显已存在表。
import asyncio
import os
import re
import sys

import asyncpg


async def main() -> None:
    mig = sys.argv[1]
    prefix = sys.argv[2] if len(sys.argv) > 2 else ""
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    sql = open(mig, encoding="utf-8-sig").read()
    # 先逐行剥 -- 注释（否则文件头注释与首条 CREATE 粘连成一条，被整体误弃——0031 踩过），
    # 再按分号拆分（DDL 无函数体，拆分安全）
    sql = re.sub(r"^--.*$", "", sql, flags=re.MULTILINE)
    stmts = [s.strip() for s in re.split(r";\s*\n", sql) if s.strip()]
    ok = 0
    for s in stmts:
        try:
            await conn.execute(s)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"STMT_FAIL: {e}\n  head={s[:80]!r}")
            await conn.close()
            sys.exit(1)
    if prefix:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE $1 ORDER BY tablename",
            prefix + "%",
        )
        print(f"executed={ok}/{len(stmts)} tables={[r['tablename'] for r in rows]}")
    else:
        print(f"executed={ok}/{len(stmts)}")
    await conn.close()


asyncio.run(main())
