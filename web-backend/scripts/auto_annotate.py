"""T-CAP1 题库标注自动化管线（L3，产能第一铲）。

对 course_kb 中**缺失标注**的题目，用 LLM 批量预标 difficulty / year / paper_type，
建议先进 course_kb_annotation_suggestions（pending 待校），人工 approve 后才写回。

为什么只标这三类字段
------------------
线上实测（2026-11-16）：257 题中 knowledge_points / type 已 100% 标注，
真正缺的是 difficulty（大量停留在默认值 3）、year（数学 46 空）、paper_type（数学 34 空）。
不重复标已有的知识点/题型，避免浪费 token 和覆盖人工已确认结果。

红线
----
* 审核隔离（M0-2）：LLM 建议一律进 pending 待校队列，不直接改 course_kb 已发布数据。
* 绝不编造：拿不准年/卷别的返回 null，不猜测；difficulty 给 1~5 整数 + 简短依据。
* 幂等续跑：唯一键 (content_hash, field_name)，已有建议的字段跳过，不重复烧 token。
* 可测：parse_suggestion / coerce_value / build_prompt 是纯函数；llm transport 可注入。

用法（在 edu-web-backend 容器内，cwd=/app）
------------------------------------------
    # 校验 prompt（不连库不调 LLM）
    python scripts/auto_annotate.py --pilot --dry-run

    # 真实跑 pilot（小样本，数学 5 题）
    python scripts/auto_annotate.py --pilot --limit 5

    # 全量补标数学（可续跑）
    python scripts/auto_annotate.py --subject 数学 --limit 0

    # 只标某一字段 / 指定物理
    python scripts/auto_annotate.py --subject 数学 --field difficulty
    python scripts/auto_annotate.py --subject 物理 --field difficulty

    # 统计待校队列
    python scripts/auto_annotate.py --stats

环境变量：DATABASE_URL、CRAWL_LLM_BASE_URL/API_KEY/MODEL（容器内已注入，qwen3.6-flash）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.ssrf import SSRFRejected, assert_safe_base_url  # noqa: E402

# 说明：本脚本是独立运维脚本，数据层走 asyncpg 裸 SQL（与 gen_pedagogy.py 同范式），
# 不依赖 ORM 模型是否已随镜像发版——这样拷进旧容器也能跑，只要迁移 0024 已执行。

PROMPT_VERSION = 1

#: 一次 LLM 调用批量标注的题数（控制 token 与 JSON 稳定性）
BATCH_SIZE = 8

FIELDS = ("difficulty", "year", "paper_type")

SYSTEM_PROMPT = (
    "你是高中题库的「标注助手」。根据提供的题目，为每道题补全指定的标注字段。\n"
    "【硬性规则】\n"
    "1. 只依据题干/选项中真实出现的信息推断；拿不准就对那个字段返回 null，严禁编造。\n"
    "2. difficulty 为 1~5 的整数（1 最易、5 最难），依据知识跨度/运算量/思维层次判断。\n"
    "3. year 为题目出处年份（4 位整数）；题干/解析未标明年份就返回 null。\n"
    "4. paper_type 为卷别（如「新高考I卷」「全国乙卷」）；无明确出处就返回 null。\n"
    "5. reason 用一句话给人工审核看依据；存疑要明说。\n"
    "6. 只输出严格 JSON，不要 markdown 围栏、不要解释文字。\n"
    "【输出格式】\n"
    '{"items":[{"id":"<题目id>","difficulty":3,"year":2023,'
    '"paper_type":"新高考I卷","reason":"题干含2023年新高考I卷字样"}]}\n'
    "未要求补全的字段返回 null 即可。id 必须与输入一致。"
)


def build_user_prompt(rows: List[Dict[str, Any]], fields: List[str]) -> str:
    """组装 user prompt：题目作为数据传入，不拼进 system。"""
    parts = [f"【需补全字段】{', '.join(fields)}", "【题目列表】"]
    for r in rows:
        stem = str(r.get("stem") or "").strip().replace("\n", " ")
        if len(stem) > 600:
            stem = stem[:600] + "…"
        analysis = str(r.get("analysis") or "").strip().replace("\n", " ")
        if len(analysis) > 300:
            analysis = analysis[:300] + "…"
        parts.append(
            f'- id={r["id"]}\n  学科={r.get("subject") or ""} 题型={r.get("type") or ""} '
            f'年级={r.get("grade") or ""}\n'
            f'  题干={stem}\n  解析={analysis or "(无)"}'
        )
    parts.append("\n请按规则只输出 JSON。")
    return "\n".join(parts)


def parse_suggestion(text: str) -> Dict[str, Dict[str, Any]]:
    """稳健解析 LLM 输出：剥离围栏/前言尾注，返回 {id: {field: value, ...}}。

    解析失败抛 ValueError，由调用方计入失败数，不中断整批。
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("模型返回为空")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", body, flags=re.S)
        candidate = fenced.group(1).strip() if fenced else body
        starts = [p for p in (candidate.find("{"), candidate.find("[")) if p >= 0]
        ends = [p for p in (candidate.rfind("}"), candidate.rfind("]")) if p >= 0]
        if not starts or not ends or min(starts) >= max(ends):
            raise ValueError("模型输出中未找到完整 JSON")
        try:
            payload = json.loads(candidate[min(starts): max(ends) + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型输出不是合法 JSON：{exc}") from exc

    raw = payload.get("items") if isinstance(payload, dict) else payload
    if isinstance(raw, dict) and not isinstance(raw, list):
        raw = next((v for v in raw.values() if isinstance(v, list)), [])
    if not isinstance(raw, list):
        raise ValueError("模型输出不是项目数组")

    out: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        if not qid:
            continue
        out[qid] = item
    return out


def coerce_value(field: str, value: Any) -> Optional[str]:
    """把建议值归一为可入库文本；不合法/为空返回 None（不写建议）。

    difficulty: 1~5 整数；year: 1900~2100 整数；paper_type: 去掉首尾空白的非空串。
    """
    if value is None or value == "":
        return None
    if field == "difficulty":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return str(n) if 1 <= n <= 5 else None
    if field == "year":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return str(n) if 1900 <= n <= 2100 else None
    if field == "paper_type":
        s = str(value).strip()
        return s if s and s.lower() != "null" else None
    return None


async def default_transport(system: str, user: str, *, timeout: float = 90.0) -> str:
    """全局 env 的 qwen3.6-flash 通道；base_url 过 SSRF 闸门，绝不退化成模板。"""
    import httpx

    base_url = settings.crawl_llm_base_url
    api_key = settings.crawl_llm_api_key
    model = settings.crawl_llm_model
    if not (base_url and api_key and model):
        raise RuntimeError("未配置 CRAWL_LLM_BASE_URL/API_KEY/MODEL")
    try:
        base_url = assert_safe_base_url(base_url)
    except SSRFRejected as exc:
        raise RuntimeError(f"模型 base_url 未通过 SSRF 闸门：{exc}") from exc

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


async def fetch_pending_rows(conn, subject: str, fields: List[str], limit: int) -> List[Dict[str, Any]]:
    """扫描需补标的题：目标字段缺失 且 状态为 published（不动 draft/已隔离脏题）。

    字段级跳过不在 SQL 里做——一题可能只缺 difficulty 但 year 已建议过，
    按题捞出后由 write_suggestions 逐字段幂等判断。
    """
    conds = []
    if "difficulty" in fields:
        conds.append("(k.difficulty IS NULL OR k.difficulty = 3)")
    if "year" in fields:
        conds.append("k.year IS NULL")
    if "paper_type" in fields:
        conds.append("(k.paper_type IS NULL OR k.paper_type = '')")
    where_field = " OR ".join(conds) if conds else "FALSE"

    sql = f"""
        SELECT k.id, k.content_hash, k.subject, k.type, k.grade, k.stem, k.analysis,
               k.difficulty, k.year, k.paper_type
        FROM course_kb k
        WHERE k.subject = $1
          AND ({where_field})
          AND k.status = 'published'
        ORDER BY k.id
        LIMIT $2
    """
    lim = limit if limit > 0 else 100000
    recs = await conn.fetch(sql, subject, lim)
    return [dict(r) for r in recs]


async def existing_fields(conn, hashes: List[str]) -> set:
    """已生成过建议的 (hash, field) 集合，用于字段级幂等。"""
    if not hashes:
        return set()
    recs = await conn.fetch(
        "SELECT content_hash, field_name FROM course_kb_annotation_suggestions "
        "WHERE content_hash = ANY($1)", hashes)
    return {(r["content_hash"], r["field_name"]) for r in recs}


async def write_suggestions(
    conn,
    parsed: Dict[str, Dict[str, Any]],
    rows: List[Dict[str, Any]],
    fields: List[str],
    already: set,
    reason_key: str = "reason",
) -> Dict[str, int]:
    """把解析结果写入 pending 待校队列（ON CONFLICT DO NOTHING 双保险），返回统计。

    审核隔离：只 INSERT 到建议表，绝不 UPDATE course_kb。
    """
    by_id = {str(r["id"]): r for r in rows}
    stats = {"proposed": 0, "written": 0, "skipped_existing": 0, "invalid": 0, "no_llm_value": 0}
    pending: List[tuple] = []
    for qid, item in parsed.items():
        row = by_id.get(qid)
        if row is None:
            stats["invalid"] += 1
            continue
        ch = row["content_hash"]
        reason = str(item.get(reason_key) or "").strip() or None
        for field in fields:
            val = coerce_value(field, item.get(field))
            if val is None:
                stats["no_llm_value"] += 1
                continue
            if (ch, field) in already:
                stats["skipped_existing"] += 1
                continue
            pending.append((ch, field, val, reason, settings.crawl_llm_model, PROMPT_VERSION))
            already.add((ch, field))
            stats["written"] += 1
            stats["proposed"] += 1
    if pending:
        await conn.executemany(
            "INSERT INTO course_kb_annotation_suggestions "
            "(content_hash, field_name, suggested_value, reason, source_model, prompt_version, status) "
            "VALUES ($1,$2,$3,$4,$5,$6,'pending') "
            "ON CONFLICT (content_hash, field_name) DO NOTHING",
            pending,
        )
    return stats


async def run(args, transport: Optional[Callable] = None) -> Dict[str, Any]:
    fields = [args.field] if args.field else list(FIELDS)
    rows_preview = [
        {"id": "1", "stem": "2023年新高考I卷第2题…", "analysis": "由复数运算得…", "subject": "数学", "type": "单选", "grade": "高三"},
        {"id": "2", "stem": "已知函数 f(x)=x²-2x…", "analysis": "", "subject": "数学", "type": "解答", "grade": "高三"},
    ]
    if args.dry_run:
        print("[dry-run] SYSTEM:\n" + SYSTEM_PROMPT)
        print("[dry-run] USER:\n" + build_user_prompt(rows_preview, fields))
        return {"dry_run": True}

    import asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not dsn and not args.dry_run:
        raise SystemExit("未配置 DATABASE_URL")

    call = transport or default_transport
    outcome: Dict[str, Any] = {"batches": 0, "llm_ok": 0, "llm_fail": 0,
                               "proposed": 0, "written": 0, "skipped_existing": 0,
                               "invalid": 0, "no_llm_value": 0, "rows_scanned": 0}
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            rows = await fetch_pending_rows(conn, args.subject, fields, args.limit)
            outcome["rows_scanned"] = len(rows)
            already = await existing_fields(conn, [r["content_hash"] for r in rows])
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                user = build_user_prompt(batch, fields)
                try:
                    raw = await call(SYSTEM_PROMPT, user)
                    parsed = parse_suggestion(raw)
                    outcome["llm_ok"] += 1
                except Exception as exc:  # noqa: BLE001 单批失败不中断整批
                    outcome["llm_fail"] += 1
                    print(f"[warn] 第 {i // BATCH_SIZE + 1} 批 LLM 失败：{exc!r}", flush=True)
                    continue
                stats = await write_suggestions(conn, parsed, batch, fields, already)
                for k in ("proposed", "written", "skipped_existing", "invalid", "no_llm_value"):
                    outcome[k] += stats[k]
                outcome["batches"] += 1
                print(f"  [batch {i // BATCH_SIZE + 1}] written={stats['written']} "
                      f"skip={stats['skipped_existing']} no_value={stats['no_llm_value']}", flush=True)
    finally:
        await pool.close()
    return outcome


async def show_stats() -> None:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            recs = await conn.fetch(
                "SELECT field_name, status, count(*)::int n FROM course_kb_annotation_suggestions "
                "GROUP BY field_name, status ORDER BY field_name, status")
            print("=== 标注建议队列 ===")
            for r in recs:
                print(f"  {r['field_name']:12} {r['status']:10} {r['n']}")
            total = await conn.fetchval("SELECT count(*) FROM course_kb_annotation_suggestions")
            print(f"  合计 {total} 条")
    finally:
        await pool.close()


def main() -> None:
    p = argparse.ArgumentParser(description="T-CAP1 题库标注自动化管线")
    p.add_argument("--subject", default="数学")
    p.add_argument("--field", choices=list(FIELDS), default=None)
    p.add_argument("--limit", type=int, default=0, help="扫描题数上限，0=不限")
    p.add_argument("--pilot", action="store_true", help="小样本模式（等价于 --limit 5）")
    p.add_argument("--dry-run", action="store_true", help="只打印 prompt，不连库不调 LLM")
    p.add_argument("--stats", action="store_true", help="只看待校队列统计，不生成建议")
    args = p.parse_args()
    if args.pilot and args.limit == 0:
        args.limit = 5
    if args.stats:
        asyncio.run(show_stats())
        return
    result = asyncio.run(run(args))
    print("[done] " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
