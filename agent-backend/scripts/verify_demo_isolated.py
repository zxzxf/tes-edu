"""验证 seed demo 假题已被 RAG 检索硬隔离。

用法（在 agent-backend 容器内）：
    python scripts/verify_demo_isolated.py

判据：用 demo 原题原文去检索，返回结果里不得再出现 demo 原文片段
（demo 题中大量使用 lg100 / log₂8 这类编造例题才会有的写法）。
"""
from __future__ import annotations

import asyncio
import sys

from app.core import rag

# 取自 course_kb_embeddings 中 is_demo=true 的原文（id 1-46）
DEMO_QUERIES = [
    "已知集合 A={x | x²-3x+2<0}，B={x | 1<x<3}，求 A∩B。",
    "计算 log₂8 + lg100。",
    "求 (1+x)⁵ 展开式中 x² 的系数。",
    "求双曲线 x²/9-y²/16=1 的渐近线方程。",
    "若 X~B(10,0.3)，求 E(X)。",
]

# demo 独有的字符串指纹，命中即说明 demo 仍在参与检索
DEMO_MARKS = ["lg100", "log₂8 + lg100", "x²-3x+2<0", "掷两枚骰子", "C(5,2)"]


async def main() -> int:
    leaked = 0
    for q in DEMO_QUERIES:
        res = await rag.retrieve(q, "数学")
        chunks = res.get("chunks", [])
        hits = [
            c["text"]
            for c in chunks
            if any(m in c["text"] for m in DEMO_MARKS)
        ]
        flag = "LEAK" if hits else "OK  "
        print(f"[{flag}] {q[:38]} -> top1: {chunks[0]['text'][:46] if chunks else '(空)'}")
        if hits:
            leaked += 1
            for h in hits:
                print(f"        命中 demo: {h[:60]}")

    print("-" * 60)
    print(f"demo 泄漏查询数：{leaked}/{len(DEMO_QUERIES)}")
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
