"""链路冒烟：验证 检索 → 讲解 → 变式 三段是否真正打通（dev 模式零依赖可跑）。

用法：
    cd agent-backend && python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config  # noqa: E402
from app.core.orchestrator import handle_question  # noqa: E402


async def main() -> None:
    print(f"[mode] {config.mode()}  (llm={config.llm_ready()}, embed={config.embed_ready()}, db={config.db_ready()})")

    question = "求函数 f(x)=x³-3x 的单调递减区间"
    out = await handle_question(question, mode="explain", subject="math")

    print(f"\n[question] {question}")
    print(f"[knowledge_points] {out.get('knowledge_points')}")
    print(f"[steps] {out.get('steps')}")
    print(f"[answer]\n{out.get('answer')}")
    print(f"\n[similar] {out.get('similar')}")

    assert out.get("answer"), "answer 不应为空"
    assert out.get("knowledge_points"), "应检索到知识点"
    print("\n[OK] 三段链路（RAG→LLM→变式）已打通")


if __name__ == "__main__":
    asyncio.run(main())
