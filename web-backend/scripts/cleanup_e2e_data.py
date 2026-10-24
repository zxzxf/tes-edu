"""清理 e2e 遗留测试数据（student_id 前缀 s5_e2e_ / e2e_）。

S5 端到端脚本异常中断时会留下测试数据，用本脚本兜底清理。
    python scripts/cleanup_e2e_data.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://tes:Tes%40Pg%23106@127.0.0.1:15432/tes_edu",
)

from sqlalchemy import delete, select, text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.models.ops import (  # noqa: E402
    StudentEvent,
    StudentMastery,
    StudentStepError,
    WrongQuestion,
)

PREFIXES = ("s5_e2e_%", "e2e_%")


async def main() -> None:
    async with SessionLocal() as db:
        for model in (StudentEvent, StudentStepError, StudentMastery, WrongQuestion):
            res = await db.execute(
                select(model.id).where(model.student_id.like(PREFIXES[0]))
            )
            ids = [r[0] for r in res.all()]
            if ids:
                await db.execute(delete(model).where(model.id.in_(ids)))
            print(f"{model.__tablename__}: 删除 {len(ids)} 行")
        await db.commit()
        # 兜底：按 tenant_id 也清一次（student_id 前缀之外的情况）
        res = await db.execute(
            text("DELETE FROM student_step_errors WHERE tenant_id LIKE 's5_e2e_%'")
        )
        print("student_step_errors(tenant兜底):", res.rowcount)
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
