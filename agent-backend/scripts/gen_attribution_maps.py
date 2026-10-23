"""步骤级归因 P1.5·归因映射内容生成器（在容器内运行）。

为 course_kb_steps 的三类映射列填充 LLM 生成内容（S3 零成本查表的燃料）：
  - step_kind='option' 且 is_correct=false -> distractor_map   （错项诱惑分析）
  - step_kind='blank'                      -> wrong_value_map  （典型错值分析）
  - step_kind IN ('procedure','subq')      -> typical_errors   （典型错误）

可续跑：只处理目标列为 NULL 的步骤；中断后重跑自动跳过已完成。
并发：题级 QCON 路并行 + LLM 调用 llm_sem 限流 + 单调用 120s 超时（防挂死）。

用法：
  python scripts/gen_attribution_maps.py --pilot                # 1 单选+1 填空+1 解答（真实写入校验）
  python scripts/gen_attribution_maps.py --pilot --dry-run      # 只打印 prompt 不调 LLM
  python scripts/gen_attribution_maps.py --subject 数学        # 数学全量
  python scripts/gen_attribution_maps.py                        # 数学 -> 物理 全量
  python scripts/gen_attribution_maps.py --subject 数学 --limit 20

需要环境变量：DB_DSN、LLM_BASE_URL、LLM_API_KEY、LLM_MODEL（容器内已注入）。
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

client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL, timeout=120)
QCON = 10                     # 题级并发
llm_sem = asyncio.Semaphore(14)  # LLM 调用总并发上限（防端点限流）

TAXONOMY = (
    "concept(概念混淆)/formula(公式误用)/case(分类讨论遗漏)/calc(计算失误)"
    "/read(审题错误)/logic(逻辑错误)/strategy(策略或方法不当)"
)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def _call_json(system: str, user: str, temp: float = 0.4) -> dict:
    last_err = None
    for attempt in range(4):
        try:
            async with llm_sem:
                resp = await client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temp,
                    timeout=120,
                )
            return _parse_json(resp.choices[0].message.content or "")
        except Exception as e:  # 限流 / 网络抖动 / 超时：指数退避重试
            last_err = e
            if attempt < 3:
                await asyncio.sleep(5 * (2 ** attempt))
            else:
                print(f"[warn] LLM 调用重试耗尽: {type(e).__name__}: {e}")
    return {}


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------
DISTRACTOR_SYSTEM = (
    "你是高考命题研究与错题归因专家。针对选择题的【错误选项】，分析学生为什么会选它："
    "要挖出认知根源（概念混淆/公式误用/审题偏差/记忆错位等），而不是简单说'粗心'。"
    "产出符合高考评价体系、可直接展示给学生的归因。"
)

WRONGVAL_SYSTEM = (
    "你是高考数学/物理错题归因专家。针对填空题，推断学生最可能写出的【典型错值】，"
    "并分析其错误类型与认知根源。错值必须是可复算出来的（如误用公式、符号错误、漏讨论、"
    "开方遗漏等得出的具体数值或表达式），不要凭空编造。"
)

TYPICAL_SYSTEM = (
    "你是高考解答题阅卷与错题归因专家。针对解答题的【每一步】，列出学生常见的典型错误，"
    "给出错误类型、认知根源、正确写法（可直接展示给学生）、以及判分线索"
    "（学生作答出现什么特征说明踩了这个错）。"
)


def _fmt_options(options) -> str:
    if not options:
        return "(无选项)"
    if isinstance(options, list):
        return "\n".join(f"{chr(65+i)}. {o}" for i, o in enumerate(options))
    if isinstance(options, dict):
        return "\n".join(f"{k}. {v}" for k, v in options.items())
    return str(options)


def _distractor_user(q: dict, opt_steps: list[dict]) -> str:
    wrong_lines = "\n".join(
        f"- 选项{s['label']} (step_no={s['step_no']}): {s['text']}" for s in opt_steps
    )
    return (
        f"【学科】{q.get('subject')}\n【题型】{q.get('type')}\n"
        f"【题干】\n{q.get('stem') or ''}\n"
        f"【选项】\n{_fmt_options(q.get('options'))}\n"
        f"【正确答案】{q.get('answer') or ''}\n"
        f"【官方解析】\n{q.get('analysis') or '(无)'}\n\n"
        f"【需要归因的错误选项】\n{wrong_lines}\n\n"
        "请为【每一个】错误选项产出归因，严格输出 JSON（不要包裹 markdown）：\n"
        '{"distractors": ['
        '{"label":"A","wrong_step":<该错项 step_no>,"error_type":"<七类之一>",'
        '"reason":"学生选它的认知根源（为什么这个选项有迷惑性）",'
        '"correction":"正确做法一句话"}'
        ", ...]}\n"
        "label 必须与上方错误选项字母一致；error_type 只能取七类之一。"
    )


def _wrongval_user(q: dict, blank_steps: list[dict]) -> str:
    blank_lines = "\n".join(
        f"- 第{idx+1}空 (step_no={s['step_no']}): {s['text']}" for idx, s in enumerate(blank_steps)
    )
    return (
        f"【学科】{q.get('subject')}\n【题型】{q.get('type')}\n"
        f"【题干】\n{q.get('stem') or ''}\n"
        f"【正确答案】{q.get('answer') or ''}\n"
        f"【官方解析】\n{q.get('analysis') or '(无)'}\n\n"
        f"【填空题步骤】\n{blank_lines}\n\n"
        "请为每个空推断 2-3 个【典型错值】（可复算得出），严格输出 JSON：\n"
        '{"blanks":[{"blank_no":<step_no>,"wrong_values":['
        '{"value":"<典型错值>","wrong_step":<step_no>,"error_type":"<七类之一>",'
        '"reason":"为什么学生会写出这个错值","correction":"正确做法"}'
        ", ...]}]}\n"
    )


def _typical_user(q: dict, proc_steps: list[dict]) -> str:
    step_lines = "\n".join(
        f"- 步骤{s['step_no']} (知识点={s.get('kp_name') or ''}): {s['text']}" for s in proc_steps
    )
    return (
        f"【学科】{q.get('subject')}\n【题型】{q.get('type')}\n"
        f"【题干】\n{q.get('stem') or ''}\n"
        f"【参考答案】{q.get('answer') or ''}\n"
        f"【官方解析】\n{q.get('analysis') or '(无)'}\n\n"
        f"【解题步骤】\n{step_lines}\n\n"
        "请为【每一步】列出 1-2 个典型错误，严格输出 JSON：\n"
        '{"steps":[{"step_no":<步骤号>,"typical_errors":['
        '{"error_type":"<七类之一>","reason":"这一步为什么会错（具体情境）",'
        '"correction":"这一步正确的完整写法，可直接展示给学生",'
        '"evidence_hint":"学生作答出现什么特征说明踩了这个错（给判分用）"}'
        ", ...]}]}\n"
    )


# ----------------------------------------------------------------------------
# 生成（单题单类）
# ----------------------------------------------------------------------------
async def gen_distractors(q, opt_steps, dry) -> list:
    if dry:
        print(f"[dry] DISTRACTOR {q['content_hash'][:8]}\n{_distractor_user(q, opt_steps)}\n{'='*60}")
        return []
    data = await _call_json(DISTRACTOR_SYSTEM, _distractor_user(q, opt_steps))
    out = data.get("distractors") or []
    return out if isinstance(out, list) else []


async def gen_wrong_values(q, blank_steps, dry) -> list:
    if dry:
        print(f"[dry] WRONGVAL {q['content_hash'][:8]}\n{_wrongval_user(q, blank_steps)}\n{'='*60}")
        return []
    data = await _call_json(WRONGVAL_SYSTEM, _wrongval_user(q, blank_steps))
    out = data.get("blanks") or []
    return out if isinstance(out, list) else []


async def gen_typical(q, proc_steps, dry) -> list:
    if dry:
        print(f"[dry] TYPICAL {q['content_hash'][:8]}\n{_typical_user(q, proc_steps)}\n{'='*60}")
        return []
    data = await _call_json(TYPICAL_SYSTEM, _typical_user(q, proc_steps))
    out = data.get("steps") or []
    return out if isinstance(out, list) else []


# ----------------------------------------------------------------------------
# 单题处理（并发单元）
# ----------------------------------------------------------------------------
async def process_question(ch: str, args, pool) -> None:
    async with pool.acquire() as conn:
        q = dict(await conn.fetchrow(
            "SELECT content_hash,subject,type,stem,options,answer,analysis,knowledge_points "
            "FROM course_kb WHERE content_hash=$1", ch))
        steps = [dict(r) for r in await conn.fetch(
            "SELECT step_no,step_kind,label,text,is_correct,kp_name,"
            "distractor_map,wrong_value_map,typical_errors "
            "FROM course_kb_steps WHERE content_hash=$1 ORDER BY step_no", ch)]

    opt_steps = [s for s in steps if s["step_kind"] == "option" and s["is_correct"] is False]
    blank_steps = [s for s in steps if s["step_kind"] == "blank"]
    proc_steps = [s for s in steps if s["step_kind"] in ("procedure", "subq")]

    # 客观题错项归因
    if opt_steps and (args.pilot or any(s["distractor_map"] is None for s in opt_steps)):
        d_out = await gen_distractors(q, opt_steps, args.dry_run)
        by_label = {str(d.get("label")): d for d in d_out}
        for s in opt_steps:
            d = by_label.get(str(s["label"]))
            if d and not args.dry_run:
                dm = {
                    "wrong_step": d.get("wrong_step") or s["step_no"],
                    "error_type": d.get("error_type"),
                    "reason": d.get("reason"),
                    "correction": d.get("correction"),
                }
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE course_kb_steps SET distractor_map=$1::jsonb "
                        "WHERE content_hash=$2 AND step_no=$3",
                        json.dumps(dm, ensure_ascii=False), ch, s["step_no"])
        print(f"  [opt✓] {ch[:8]} 错项={len(opt_steps)}")

    # 填空错值归因
    if blank_steps and (args.pilot or any(s["wrong_value_map"] is None for s in blank_steps)):
        w_out = await gen_wrong_values(q, blank_steps, args.dry_run)
        by_no = {str(b.get("blank_no")): b for b in w_out}
        for s in blank_steps:
            b = by_no.get(str(s["step_no"]))
            if b and not args.dry_run:
                wvm = [
                    {
                        "value": w.get("value"),
                        "wrong_step": w.get("wrong_step") or s["step_no"],
                        "error_type": w.get("error_type"),
                        "reason": w.get("reason"),
                        "correction": w.get("correction"),
                    }
                    for w in (b.get("wrong_values") or [])
                ]
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE course_kb_steps SET wrong_value_map=$1::jsonb "
                        "WHERE content_hash=$2 AND step_no=$3",
                        json.dumps(wvm, ensure_ascii=False), ch, s["step_no"])
        print(f"  [blank✓] {ch[:8]} 空数={len(blank_steps)}")

    # 解答题典型错误
    if proc_steps and (args.pilot or any(s["typical_errors"] is None for s in proc_steps)):
        t_out = await gen_typical(q, proc_steps, args.dry_run)
        by_no = {str(t.get("step_no")): t for t in t_out}
        for s in proc_steps:
            t = by_no.get(str(s["step_no"]))
            if t and not args.dry_run:
                tes = [
                    {
                        "error_type": e.get("error_type"),
                        "reason": e.get("reason"),
                        "correction": e.get("correction"),
                        "evidence_hint": e.get("evidence_hint"),
                    }
                    for e in (t.get("typical_errors") or [])
                ]
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE course_kb_steps SET typical_errors=$1::jsonb "
                        "WHERE content_hash=$2 AND step_no=$3",
                        json.dumps(tes, ensure_ascii=False), ch, s["step_no"])
        print(f"  [proc✓] {ch[:8]} 步数={len(proc_steps)}")


# ----------------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------------
async def run(args):
    import asyncpg

    dsn = os.environ.get("DB_DSN")
    if not dsn:
        raise SystemExit("未配置 DB_DSN，无法连库。")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=24)

    subjects = [args.subject] if args.subject else (["数学"] if args.pilot else ["数学", "物理"])
    q_sem = asyncio.Semaphore(QCON)

    async def _guarded(ch):
        async with q_sem:
            try:
                await process_question(ch, args, pool)
            except Exception as e:
                print(f"  [err] {ch[:8]} 跳过: {type(e).__name__}: {e}")

    for subj in subjects:
        async with pool.acquire() as conn:
            if args.pilot:
                q_hashes = []
                for t in ("单选", "填空", "解答"):
                    r = await conn.fetchrow(
                        "SELECT content_hash FROM course_kb "
                        "WHERE source_tag='gaokao_real' AND subject=$1 AND type=$2 "
                        "AND content_hash IN (SELECT content_hash FROM course_kb_steps) "
                        "ORDER BY id LIMIT 1",
                        subj, t,
                    )
                    if r:
                        q_hashes.append(r["content_hash"])
            else:
                recs = await conn.fetch(
                    """SELECT content_hash FROM course_kb_steps
                       WHERE content_hash IN (
                           SELECT content_hash FROM course_kb
                           WHERE source_tag='gaokao_real' AND subject=$1)
                       GROUP BY content_hash
                       HAVING bool_or(step_kind='option' AND is_correct=false AND distractor_map IS NULL)
                          OR bool_or(step_kind='blank' AND wrong_value_map IS NULL)
                          OR bool_or(step_kind IN ('procedure','subq') AND typical_errors IS NULL)
                       ORDER BY content_hash""",
                    subj,
                )
                q_hashes = [r["content_hash"] for r in recs]
                if args.limit:
                    q_hashes = q_hashes[: args.limit]

        print(f"[plan] {subj}: {len(q_hashes)} 题待处理")
        await asyncio.gather(*[_guarded(ch) for ch in q_hashes])

    await pool.close()
    print("[done]")


def main():
    if not config.llm_ready():
        raise SystemExit("LLM 未就绪（缺 LLM_BASE_URL/KEY/MODEL）。")
    p = argparse.ArgumentParser()
    p.add_argument("--subject", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
