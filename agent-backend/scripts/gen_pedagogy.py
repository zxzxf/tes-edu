"""步骤级归因·教学内容生成器（在 edu-agent-backend 容器内运行）。

分层生成：
  Pass A  知识点层  -> kb_knowledge_points.understanding / memory / analogy
  Pass B  步骤层    -> course_kb_steps.explanation / error_reason

用法：
  # pilot（1 单选 + 1 填空 + 1 解答 + 其涉及的知识点）
  python scripts/gen_pedagogy.py --pilot

  # 全量（数学）
  python scripts/gen_pedagogy.py --subject 数学 --kp-limit 0 --q-limit 0

  # 仅知识点 / 仅步骤
  python scripts/gen_pedagogy.py --subject 数学 --kp-only
  python scripts/gen_pedagogy.py --subject 物理 --q-only --q-limit 30

  # 仅打印 prompt 不调用 LLM（校验 prompt 设计）
  python scripts/gen_pedagogy.py --pilot --dry-run

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

client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
CONCUR = 8
sem = asyncio.Semaphore(CONCUR)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def _call_json(system: str, user: str, temp: float = 0.4) -> dict:
    """调用 LLM 并解析 JSON。

    注意：当前端点上的模型多为推理型（reasoning），其 thinking 过程会消耗大量
    completion token：
      - 不可设置较小的 max_tokens，否则思考预算被吃光、返回空内容；
      - 单条耗时可达 30-60s，并发时更久，故超时放宽到 180s；
      - 返回空内容（未解析出 JSON）同样视为失败并重试。
    """
    last = None
    for attempt in range(3):
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=config.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temp,
                    timeout=180,
                )
            data = _parse_json(resp.choices[0].message.content or "")
            if data:
                return data
            last = RuntimeError("empty response (可能被 max_tokens/思考预算截断)")
        except Exception as e:  # noqa: BLE001 重试网络/限流错误，单条失败不中断整批
            last = e
        if attempt < 2:
            await asyncio.sleep(3 * (attempt + 1))
    print(f"[warn] LLM 调用失败，跳过该条: {last!r}")
    return {}


# ----------------------------------------------------------------------------
# Pass A：知识点层
# ----------------------------------------------------------------------------
KP_SYSTEM = (
    "你是一名资深高中学科教研员，熟悉高考评价体系。请基于【知识点原文】"
    "产出三段可直接用于'学生自学 + 教师备课'的教学内容，语言准确、通俗、可操作，"
    "不要编造公式，不要输出与知识点无关的内容。"
)


def _kp_user(row: dict) -> str:
    return (
        f"【学科】{row.get('subject') or ''}\n"
        f"【章节】{row.get('chapter') or ''}\n"
        f"【知识点名称】{row.get('title') or ''}\n"
        f"【知识点原文定义】\n{row.get('content') or '(无原文，请基于名称合理展开)'}\n\n"
        "请严格输出 JSON（不要包裹 markdown）：\n"
        "{\n"
        '  "understanding": "知识点如何理解：用学生能懂的语言讲清它是什么、为什么这样定义、'
        "和哪些已学知识有联系；并补充一段教师视角的教学提示（重难点、易混点）。\",\n"
        '  "memory": "记忆方法：一条易记的口诀 / 联想 / 记忆锚点，帮助学生长期记住该知识点。\",\n'
        '  "analogy": "触类旁通：用一道类比或迁移示例，说明这个知识点如何迁移到其它相似题型，'
        '让学生学会举一反三。"\n}\n'
    )


async def gen_kp(row: dict, dry: bool) -> tuple[str, str, str]:
    if dry:
        print(f"[dry] KP {row['code']} {row['title']}\n{_kp_user(row)}\n{'='*60}")
        return "", "", ""
    data = await _call_json(KP_SYSTEM, _kp_user(row), temp=0.4)
    return (
        (data.get("understanding") or "").strip(),
        (data.get("memory") or "").strip(),
        (data.get("analogy") or "").strip(),
    )


# ----------------------------------------------------------------------------
# Pass B：步骤层
# ----------------------------------------------------------------------------
STEP_SYSTEM = (
    "你是一名高中学科导师，擅长把解题过程讲透。请基于【题目 + 已知步骤结构 + 参考答案解析】，"
    "为每一个步骤产出'详解'与（针对错误项的）'错误原因'。要求：\n"
    "1) 详解 = 该步骤的推导过程 + 涉及知识点的串联（为什么这步走、关联到哪个知识点），"
    "并点明这一步在整体解法中的作用；\n"
    "2) 对选择题/填空题：正确项说明'为什么对'并串联知识点，错误项在 error_reason 说明"
    "'为什么错/容易在哪栽'；\n"
    "3) 对解答题：每一步 explanation 给出正确推导，error_reason 给出该步常见错误；\n"
    "4) 语言面向学生，通俗且准确，不编造。"
)


def _fmt_options(options) -> str:
    if not options:
        return "(无选项)"
    if isinstance(options, list):
        return "\n".join(f"{chr(65+i)}. {o}" for i, o in enumerate(options))
    if isinstance(options, dict):
        return "\n".join(f"{k}. {v}" for k, v in options.items())
    return str(options)


def _step_user(q: dict, steps: list[dict]) -> str:
    st_lines = []
    for s in steps:
        corr = "（正确项）" if s.get("is_correct") is True else ("（错误项）" if s.get("is_correct") is False else "")
        kp = s.get("kp_name") or ""
        st_lines.append(
            f"- label={s.get('label')} kind={s.get('step_kind')} {corr} "
            f"知识点={kp}\n  内容：{s.get('text') or ''}"
        )
    return (
        f"【学科】{q.get('subject')}\n【题型】{q.get('type')}\n"
        f"【题干】\n{q.get('stem') or ''}\n"
        f"【选项】\n{_fmt_options(q.get('options'))}\n"
        f"【参考答案】{q.get('answer') or ''}\n"
        f"【官方解析】\n{q.get('analysis') or '(无)'}\n\n"
        "【已知步骤结构（请逐条补全 explanation / error_reason）】\n"
        + "\n".join(st_lines)
        + "\n\n请严格输出 JSON（不要包裹 markdown）：\n"
        '{"steps": [{"label": "A", "explanation": "...", "error_reason": "..."}, ...]}\n'
        "label 必须与上方一致；正确项的 error_reason 可填空字符串。"
    )


async def gen_question(q: dict, steps: list[dict], dry: bool) -> list[dict]:
    if dry:
        print(f"[dry] Q {q['content_hash'][:8]} {q['type']}\n{_step_user(q, steps)}\n{'='*60}")
        return []
    data = await _call_json(STEP_SYSTEM, _step_user(q, steps), temp=0.3)
    out = data.get("steps") or []
    if isinstance(out, dict):
        out = [out]
    return out


def _match_step(out_steps: list[dict], label: str, idx: int) -> dict:
    for s in out_steps:
        if str(s.get("label")) == str(label):
            return s
    if idx < len(out_steps):
        return out_steps[idx]
    return {}


# ----------------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------------
async def run(args):
    import asyncpg

    dsn = os.environ.get("DB_DSN")
    if not dsn:
        raise SystemExit("未配置 DB_DSN，无法连库。")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=16)

    kp_rows, q_rows = [], []
    async with pool.acquire() as conn:
        if args.pilot:
            # 1 单选 + 1 填空 + 1 解答
            for t in ("单选", "填空", "解答"):
                r = await conn.fetchrow(
                    "SELECT content_hash, subject, type, stem, options, answer, analysis, knowledge_points "
                    "FROM course_kb WHERE source_tag='gaokao_real' AND subject='数学' AND type=$1 "
                    "AND content_hash IN (SELECT content_hash FROM course_kb_steps) ORDER BY id LIMIT 1",
                    t,
                )
                if r:
                    q_rows.append(dict(r))
            # 这些题涉及的知识点
            codes = set()
            for q in q_rows:
                kps = q.get("knowledge_points") or []
                if isinstance(kps, str):
                    try:
                        kps = json.loads(kps)
                    except Exception:
                        kps = []
                for k in kps:
                    if isinstance(k, dict):
                        codes.add(k.get("code") or k.get("id"))
                    elif isinstance(k, str):
                        codes.add(k)
            if codes:
                recs = await conn.fetch(
                    "SELECT id, code, title, content, chapter, subject FROM kb_knowledge_points "
                    "WHERE code = ANY($1) ORDER BY id", list(codes)
                )
                kp_rows = [dict(r) for r in recs]
        else:
            subj = args.subject
            if not args.q_only:
                lim = args.kp_limit or 10_000
                kp_rows = [dict(r) for r in await conn.fetch(
                    "SELECT id, code, title, content, chapter, subject FROM kb_knowledge_points "
                    "WHERE subject=$1 AND understanding IS NULL ORDER BY id LIMIT $2", subj, lim)]
            if not args.kp_only:
                lim = args.q_limit or 10_000
                q_rows = [dict(r) for r in await conn.fetch(
                    "SELECT content_hash, subject, type, stem, options, answer, analysis, knowledge_points "
                    "FROM course_kb WHERE source_tag='gaokao_real' AND subject=$1 "
                    "AND content_hash IN (SELECT content_hash FROM course_kb_steps) "
                    "AND content_hash IN (SELECT content_hash FROM course_kb_steps "
                    "   WHERE explanation IS NULL OR explanation='' GROUP BY content_hash) "
                    "ORDER BY id LIMIT $2",
                    subj, lim)]

    print(f"[plan] 知识点 {len(kp_rows)} 个，题目 {len(q_rows)} 道")

    async def process_kp(row):
        u, m, a = await gen_kp(row, args.dry_run)
        if not args.dry_run and (u or m or a):
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE kb_knowledge_points SET understanding=$1, memory=$2, analogy=$3 WHERE id=$4",
                    u, m, a, row["id"],
                )
            print(f"  [kp✓] {row['code']} {row['title'][:14]}", flush=True)
        return True

    async def process_question(q):
        steps = []
        async with pool.acquire() as conn:
            recs = await conn.fetch(
                "SELECT step_no, step_kind, label, text, is_correct, kp_name FROM course_kb_steps "
                "WHERE content_hash=$1 ORDER BY step_no", q["content_hash"])
            steps = [dict(r) for r in recs]
        out = await gen_question(q, steps, args.dry_run)
        if args.dry_run:
            return True
        for i, s in enumerate(steps):
            o = _match_step(out, s["label"], i)
            exp = (o.get("explanation") or "").strip()
            err = (o.get("error_reason") or "").strip()
            if exp or err:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE course_kb_steps SET explanation=$1, error_reason=$2 "
                        "WHERE content_hash=$3 AND step_no=$4",
                        exp, err, q["content_hash"], s["step_no"])
        print(f"  [q✓] {q['content_hash'][:8]} {q['type']} 步数={len(steps)}", flush=True)
        return True

    # Pass A（并发）
    if kp_rows:
        await asyncio.gather(*[process_kp(r) for r in kp_rows])
    # Pass B（并发）
    if q_rows:
        await asyncio.gather(*[process_question(q) for q in q_rows])

    await pool.close()
    print("[done]")


def main():
    if not config.llm_ready():
        raise SystemExit("LLM 未就绪（缺 LLM_BASE_URL/KEY/MODEL）。")
    p = argparse.ArgumentParser()
    p.add_argument("--subject", default="数学")
    p.add_argument("--kp-limit", type=int, default=0)
    p.add_argument("--q-limit", type=int, default=0)
    p.add_argument("--kp-only", action="store_true")
    p.add_argument("--q-only", action="store_true")
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
