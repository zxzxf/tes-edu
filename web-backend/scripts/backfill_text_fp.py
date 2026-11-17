"""回填 course_kb.text_fp（T-CAP4 v5.23，迁移 0027 配套）。

为什么不用 SQL 回填：text_fingerprint = sha1(norm_text(stem) 去标点空白)，
norm_text 是全角/繁简/数学符号归一的一串 Python 规则，SQL 重写一遍必然漂移，
一旦回填值和运行时算出来的不一致，索引查找就会漏判——所以两份必须用同一函数。

用法（容器内 cwd=/app）：
    python scripts/backfill_text_fp.py --dry-run   # 只统计有多少行要回填
    python scripts/backfill_text_fp.py --apply     # 真回填（幂等，只写值为空或不同的行）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.intake_guard import text_fingerprint  # noqa: E402


async def run(*, apply: bool, batch_size: int = 200) -> Dict[str, Any]:
    import asyncpg

    url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not url:
        raise RuntimeError("DATABASE_URL 未设置")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch("SELECT id, stem, text_fp FROM course_kb ORDER BY id")
        todo: List[tuple] = []
        for r in rows:
            fp = text_fingerprint(r["stem"])
            if fp and fp != (r["text_fp"] or ""):
                todo.append((r["id"], fp))
        stats = {
            "total": len(rows), "to_backfill": len(todo),
            "applied": 0, "samples": [{"id": i, "text_fp": fp} for i, fp in todo[:5]],
        }
        if apply and todo:
            for start in range(0, len(todo), batch_size):
                batch = todo[start:start + batch_size]
                await conn.executemany(
                    "UPDATE course_kb SET text_fp = $2 WHERE id = $1", batch
                )
                stats["applied"] += len(batch)
        return stats
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="回填 course_kb.text_fp")
    ap.add_argument("--dry-run", action="store_true", help="只读统计（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认只读）")
    ap.add_argument("--batch-size", type=int, default=200)
    args = ap.parse_args()
    print(json.dumps(
        asyncio.run(run(apply=args.apply, batch_size=args.batch_size)),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
