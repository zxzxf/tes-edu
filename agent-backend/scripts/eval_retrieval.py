"""检索质量抽测：验证 RAG 召回的知识点是否命中预期。

对应任务计划书 P1 的 DoD「人工抽测 50 题的知识点召回准确率 ≥ 85%」——
本脚本先用小样本自动化跑，后续扩充 CASES 即可逼近该指标。

用法：
    cd agent-backend && python scripts/eval_retrieval.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config, rag  # noqa: E402

# (问题, 期望命中的知识点) —— 与 data/seed_questions.json 的 knowledge_point 对齐
CASES: list[tuple[str, str]] = [
    ("求椭圆 x²/25+y²/16=1 的焦点坐标", "椭圆的几何性质"),
    ("掷两枚骰子点数和为7的概率是多少", "古典概型"),
    ("计算复数 (1+i)² 的值", "复数运算"),
    ("已知等比数列首项1公比2求前5项和", "等比数列求和"),
    ("求函数 f(x)=x³-3x 的单调递减区间", "导数与单调性"),
    ("判断 x>1 是 x²>1 的什么条件", "充分条件与必要条件"),
    ("求抛物线 y²=8x 的焦点坐标和准线方程", "抛物线的焦点与准线"),
    ("已知 x>0，求 x+1/x 的最小值", "基本不等式"),
]


async def main() -> None:
    await rag.warm()
    print(f"embed_ready={config.embed_ready()} db_ready={config.db_ready()} TOP_K={config.TOP_K}")

    hit = 0
    for q, expect in CASES:
        out = await rag.retrieve(q, subject="math")
        top = out["chunks"][0] if out["chunks"] else None
        ok = bool(top and top["knowledge_point"] == expect)
        hit += ok
        flag = "OK  " if ok else "MISS"
        if top:
            print(f"{flag} top1[{top['score']}] {top['knowledge_point']}  期望:{expect}  | {q}")
        else:
            print(f"{flag} 无召回  | {q}")

    total = len(CASES)
    rate = hit / total * 100
    print(f"\nTOP1 知识点命中率: {hit}/{total} = {rate:.0f}%  (DoD 目标 ≥85%)")


if __name__ == "__main__":
    asyncio.run(main())
