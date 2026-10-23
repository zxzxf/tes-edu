"""taxonomy 批量分类：给 course_kb_steps.error_reason 打七类错因标签。

taxonomy（Schema v1 定稿，冻结）：
  concept   概念错误（定义/定理理解错）
  formula   公式误用（记错/用错条件）
  case      漏 case（分类讨论不完整）
  calc      计算错误（思路对、算错）
  read      审题错误（漏条件/看错对象）
  logic     逻辑跳步（循环论证/充分必要混淆/不取交集）
  strategy  方法错误（路线走不通/选错工具）
  unknown   无法归类（不硬猜）

只对已有 error_reason 文本的步骤分类（不生成内容，纯打标）。
用法：
  python scripts/classify_error_type.py --dry-run   # 只看计划
  python scripts/classify_error_type.py             # 执行
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import AsyncOpenAI  # noqa: E402

from app.core import config  # noqa: E402

client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
TAXONOMY = ["concept", "formula", "case", "calc", "read", "logic", "strategy", "unknown"]

SYSTEM = (
    "你是错因分类器。给你一段'学生错误原因'描述，从固定分类中选最贴切的一个。\n"
    "分类表：\n"
    "  concept  概念错误（定义/定理/性质理解错）\n"
    "  formula  公式误用（记错公式/用错适用条件，如双曲线用椭圆关系）\n"
    "  case     漏 case（分类讨论不完整/漏段/漏情形）\n"
    "  calc     计算错误（思路正确但算错：符号/抄错/化简错）\n"
    "  read     审题错误（漏条件/看错对象/未排序/忽略区间端点）\n"
    "  logic    逻辑跳步（循环论证/充要混淆/结论不与前提取交集）\n"
    "  strategy 方法错误（路线走不通/选错工具/方向性错误）\n"
    "  unknown  描述含糊无法归类\n"
    '严格输出 JSON：{"error_type": "七类之一"}。不确定就选 unknown，禁止硬猜。'
)


def _parse(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    try:
        v = (json.loads(text) or {}).get("error_type", "")
    except json.JSONDecodeError:
        v = ""
    return v if v in TAXONOMY else "unknown"


async def classify_one(reason: str, sem: asyncio.Semaphore) -> str:
    async with sem:
        try:
            resp = await client.chat.completions.create(
                model=config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"错误原因描述：{reason[:1500]}"},
                ],
                temperature=0,
            )
            return _parse(resp.choices[0].message.content or "")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] classify failed: {e}")
            return "unknown"


async def run(args):
    import asyncpg

    dsn = os.environ.get("DB_DSN")
    if not dsn:
        raise SystemExit("未配置 DB_DSN。")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content_hash, step_no, error_reason FROM course_kb_steps "
            "WHERE error_reason IS NOT NULL AND error_reason <> '' AND error_type IS NULL "
            "ORDER BY content_hash, step_no LIMIT $1",
            args.limit or 10000,
        )
    print(f"[plan] 待分类 {len(rows)} 步")
    if args.dry_run or not rows:
        await pool.close()
        return

    sem = asyncio.Semaphore(args.concur)
    results = await asyncio.gather(*(classify_one(r["error_reason"], sem) for r in rows))
    from collections import Counter

    dist = Counter(results)
    print(f"[dist] {dict(dist)}")

    n = 0
    async with pool.acquire() as conn:
        for r, et in zip(rows, results):
            await conn.execute(
                "UPDATE course_kb_steps SET error_type=$1 WHERE content_hash=$2 AND step_no=$3",
                et, r["content_hash"], r["step_no"],
            )
            n += 1
    await pool.close()
    print(f"[done] 已写入 {n} 条 error_type")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concur", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
