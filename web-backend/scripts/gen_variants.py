"""T-CAP2 变式生成器（L4 核心，产能倍增器）。

真题干 → LLM-A 生成同 KP 变式（换数/换景，答案已知）
       → LLM-B 盲解（不看 A 的答案，独立重解）
       → equiv.py 答案等价校验
       → 通过入 draft(source_tag=variant_auto)，失败 quarantine，
         非数值类标 manual_review（进 draft 但强制人工复核）。

红线（出题产能提升方案 §五）
--------------------------
1. 强制同 KP + 答案已知：只从 published 且有答案的真题生成变式，不凭空产题。
2. 双模型交叉验证：LLM-B 看不到 LLM-A 的答案，独立求解；两答案经 equiv 比对，
   不凭 LLM 自述"已校验"入库。
3. 验证覆盖边界：equiv.py 只覆盖数值/代数等价。几何证明、应用题、开放题等
   非数值类标 manual_review，须人工复核或抽样审计，不得自动发布。
4. 审核隔离：产物一律 draft/quarantine，绝不直接 published；发布仍走
   POST /exam/questions/{id}/review（M4 教师确认）。
5. 幂等续跑：content_hash = sha256(stem|type|answer)，与 parser.py 同口径；
   已存在的 hash 跳过，不重复烧 token。

用法（在 edu-web-backend 容器内，cwd=/app）
------------------------------------------
    # 校验 prompt（不连库不调 LLM）
    python scripts/gen_variants.py --dry-run

    # 真实 pilot（小样本：2 道源题各 1 变式）
    python scripts/gen_variants.py --pilot

    # 全量铺开（每道源题 N 个变式，可续跑）
    python scripts/gen_variants.py --limit 0 --per-source 2 --subject 数学

环境变量：DATABASE_URL、CRAWL_LLM_BASE_URL/API_KEY/MODEL（容器内 qwen3.6-flash）。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.ssrf import SSRFRejected, assert_safe_base_url  # noqa: E402
from app.core import equiv as eq  # noqa: E402

PROMPT_VERSION = 1
DEFAULT_TASK_ID = "tcap2_pilot_01"
TENANT_ID = "school_pilot_01"

# 单批送 LLM 的源题数
BATCH_SIZE = 4
# 每题生成变式数（可被命令行覆盖）
DEFAULT_PER_SOURCE = 1

SYSTEM_GENERATE = (
    "你是高中题库的「变式出题助手」。给定一道真题，生成**同类变式题**。\n"
    "【硬性规则】\n"
    "1. 变式必须与原真题**同一知识点、同一题型、同一难度层次**，只改数字或改情境（换数/换景），"
    "不得改变考查的核心方法。\n"
    "2. 变式题干必须**自包含**：不出现「同上」「如图」等依赖原题的表述；选项/空格完整给出。\n"
    "3. 你必须**自己算出变式的正确答案**，不得照抄原题答案（数字已变则答案必变）。\n"
    "4. 只依据题干信息，拿不准答案就返回 null，严禁编造。\n"
    "5. 只输出严格 JSON，不要 markdown 围栏、不要解释文字。\n"
    "【输出格式】\n"
    '{"items":[{"source_id":"<源题id>","stem":"<变式题干>","options":["A...","B..."],'
    '"answer":"<变式答案>","analysis":"<简要解析>","change":"换数/换景说明"}]}\n'
    "非选择题 options 返回 null。source_id 必须与输入一致。"
)

SYSTEM_SOLVE = (
    "你是高中题库的「盲解助手」。独立求解下面每道题，给出正确答案。\n"
    "【硬性规则】\n"
    "1. 只依据题干，独立计算，**不要参考任何外部给出的答案**。\n"
    "2. 选择题给选项字母，填空题给最终值，解答题给最终结果；多解用「或」连接。\n"
    "3. 拿不准就对 answer 返回 null，严禁编造。\n"
    "4. 只输出严格 JSON，不要 markdown 围栏、不要解释文字。\n"
    "【输出格式】\n"
    '{"items":[{"id":"<题目id>","answer":"<你的答案>","analysis":"<简要推导>"}]}\n'
    "id 必须与输入一致。"
)


# ---------------------------------------------------------------- 纯函数
def build_generate_prompt(rows: List[Dict[str, Any]], per_source: int) -> str:
    """组装 LLM-A 输入：源题作为数据传入，不拼进 system。"""
    parts = [f"【每道题生成变式数】{per_source}", "【源题列表】"]
    for r in rows:
        stem = _clip(str(r.get("stem") or ""), 600)
        analysis = _clip(str(r.get("analysis") or ""), 300)
        options = r.get("options")
        opt_s = json.dumps(options, ensure_ascii=False) if isinstance(options, (list, dict)) else ""
        parts.append(
            f'- id={r["id"]}\n  题干={stem}\n  题型={r.get("type") or ""} '
            f'知识点={json.dumps(r.get("knowledge_points") or [], ensure_ascii=False)}\n'
            f'  选项={opt_s or "(无)"}\n  原题答案={r.get("answer") or "(无)"}\n  解析={analysis or "(无)"}'
        )
    parts.append("\n请按规则只输出 JSON。")
    return "\n".join(parts)


def build_solve_prompt(variants: List[Dict[str, Any]]) -> str:
    """组装 LLM-B 盲解输入：只给变式题干+选项，**绝不带入 A 的答案**。"""
    parts = ["【待求解题目列表】"]
    for v in variants:
        options = v.get("options")
        opt_s = json.dumps(options, ensure_ascii=False) if isinstance(options, (list, dict)) else ""
        parts.append(
            f'- id={v["id"]}\n  题干={_clip(str(v.get("stem") or ""), 600)}\n'
            f'  题型={v.get("type") or ""}\n  选项={opt_s or "(无)"}'
        )
    parts.append("\n请按规则只输出 JSON。")
    return "\n".join(parts)


def _clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s[:n] + "…" if len(s) > n else s


def _sanitize_json_escapes(s: str) -> str:
    """修复模型输出里的非法 JSON 反斜杠转义（如 LaTeX `\\(` / `\\s` 未被双写）。

    JSON 字符串只认 ``\\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX``；其余 ``\\x``
    均属非法（实测 10 批里 1 批因题干含 `\\(` 触发 Invalid \\escape 整批失败）。
    这里把非法反斜杠补成双写，让 json.loads 能过——只改转义，不改内容语义。
    """
    legal = set('"\\/bfnrtu')
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt not in legal:
                out.append("\\\\")          # 非法转义：补成字面反斜杠
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_tolerant(body: str):
    """先直接解析，失败再清洗非法转义后重试。"""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return json.loads(_sanitize_json_escapes(body))


def parse_items(text: str, id_key: str = "id") -> Dict[str, Dict[str, Any]]:
    """稳健解析 LLM 输出：裸 JSON / 围栏 / 前言尾注 / 非法转义 / 数组或对象包裹。"""
    body = (text or "").strip()
    if not body:
        raise ValueError("模型返回为空")
    try:
        payload = _loads_tolerant(body)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", body, flags=re.S)
        candidate = fenced.group(1).strip() if fenced else body
        starts = [p for p in (candidate.find("{"), candidate.find("[")) if p >= 0]
        ends = [p for p in (candidate.rfind("}"), candidate.rfind("]")) if p >= 0]
        if not starts or not ends or min(starts) >= max(ends):
            raise ValueError("模型输出中未找到完整 JSON")
        try:
            payload = _loads_tolerant(candidate[min(starts): max(ends) + 1])
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
        key = str(item.get(id_key) or "").strip()
        if key:
            out[key] = item
    return out


def content_hash(stem: str, qtype: str, answer: str) -> str:
    """与 parser.py 同口径的题指纹：sha256(stem|type|answer)。"""
    fp = f"{stem}|{qtype}|{answer or ''}".encode("utf-8")
    return hashlib.sha256(fp).hexdigest()


def _choice_options(variant: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """把 options 列表转成 equiv.choice_equivalent 需要的 {label: content}。"""
    opts = variant.get("options")
    if not isinstance(opts, (list, tuple)):
        return None
    mapping: Dict[str, str] = {}
    labels = "ABCDEFGH"
    for i, opt in enumerate(opts):
        if i < len(labels):
            # 剥掉选项内容里的标签前缀（"C.3" -> "3"），否则按内容比对时
            # values_equivalent("3", "c.3") 判不出等价（真实题库选项常带标签）
            content = re.sub(r"^\s*[A-Ha-h][\.、\)\s]+", "", str(opt)).strip()
            mapping[labels[i]] = content or str(opt)
    return mapping or None


def answers_equivalent(variant: Dict[str, Any], answer_a: Any, answer_b: Any) -> Tuple[bool, str]:
    """用 equiv.py 比对两答案，返回 (是否等价, 判据说明)。

    - 选择题（有 options）：走 choice_equivalent（标签或选项内容比对）；
    - 填空/解答：先归一化字符串相等，再数值等价（分数/根式/区间）；
    - 任一方为 null 或无法解析：返回 False（不通过，转 quarantine/manual）。
    """
    if answer_a is None or answer_b is None:
        return False, "存在 null 答案"
    sa, sb = str(answer_a).strip(), str(answer_b).strip()
    if not sa or not sb:
        return False, "存在空答案"
    options = _choice_options(variant)
    if options:
        verdict = eq.choice_equivalent(sa, sb, options=options)
        if verdict is True:
            return True, "choice_equivalent"
        # 学生/A 写选项内容而非字母时，choice_equivalent 内部已按 options 比对
        return False, f"choice_not_equal({sa} vs {sb})"
    # 数值/代数/区间
    if eq.norm_text(sa) == eq.norm_text(sb):
        return True, "normalized_equal"
    va, vb = eq.to_number(sa), eq.to_number(sb)
    if va is not None and vb is not None:
        if eq._num_eq(va, vb):
            return True, "numeric_equal"
        return False, f"numeric_not_equal({va} vs {vb})"
    na, nb = eq.normalize_interval(sa), eq.normalize_interval(sb)
    if na is not None and nb is not None:
        return (True, "interval_equal") if na == nb else (False, f"interval_not_equal({na} vs {nb})")
    # 多解：逐项切分后集合等价
    parts_a, parts_b = eq.split_multi(sa), eq.split_multi(sb)
    if len(parts_a) > 1 or len(parts_b) > 1:
        set_a = {eq.norm_text(x) for x in parts_a}
        set_b = {eq.norm_text(x) for x in parts_b}
        if set_a == set_b:
            return True, "multi_equal"
    return False, f"non_numeric_unverified({sa} vs {sb})"


def is_numeric_gradeable(answer: Any, options: Any) -> bool:
    """判据：该变式答案能否被 equiv 确定性判定。

    数值（含分数/根式/区间/多解数值）或选择题 → True；
    纯文字/证明/开放题 → False（转 manual_review，不自动发布）。
    """
    if isinstance(options, (list, tuple)) and options:
        return True
    if answer is None or not str(answer).strip():
        return False
    s = str(answer).strip()
    if eq.to_number(s) is not None:
        return True
    if eq.normalize_interval(s) is not None:
        return True
    parts = eq.split_multi(s)
    return bool(parts) and all(eq.to_number(p) is not None for p in parts)


# ---------------------------------------------------------------- LLM transport
async def _chat(system: str, user: str, transport: Optional[Callable], timeout: float) -> str:
    call = transport or default_transport
    return await call(system, user, timeout=timeout)


async def _chat_parsed(
    system: str, user: str, id_key: str,
    transport: Optional[Callable], timeout: float, retries: int = 1,
) -> Dict[str, Dict[str, Any]]:
    """调用 LLM + 解析，偶发输出故障（非法转义/截断/围栏异常）自动重试。

    实测 10 批里 2 批遇到模型输出质量问题（Invalid \\escape / Unterminated string），
    均为偶发；重试 1 次即可恢复，单批成本可控。重试耗尽才抛，由调用方计入 llm_fail。
    """
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            raw = await _chat(system, user, transport, timeout)
            return parse_items(raw, id_key=id_key)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                print(f"  [retry {attempt + 1}] LLM 输出解析失败：{exc!r}", flush=True)
    raise last  # type: ignore[misc]


async def default_transport(system: str, user: str, *, timeout: float = 90.0) -> str:
    """全局 env 的 qwen3.6-flash 通道；base_url 过 SSRF 闸门。"""
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
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


# ---------------------------------------------------------------- DB 薄层
async def fetch_source_rows(conn, subject: str, limit: int, only_kp: Optional[str] = None):
    """取 published 且有答案的真题作为变式源（答案已知红线）。"""
    conds = ["k.status = 'published'", "k.answer IS NOT NULL", "btrim(k.answer) <> ''"]
    params: List[Any] = [subject]
    if only_kp:
        conds.append("knowledge_points::jsonb ? $2")
        params.append(only_kp)
    where = " AND ".join(conds)
    sql = f"""
        SELECT k.id, k.subject, k.type, k.grade, k.stem, k.options, k.answer,
               k.analysis, k.knowledge_points, k.content_hash
        FROM course_kb k
        WHERE k.subject = $1 AND {where}
        ORDER BY k.id
        LIMIT ${len(params) + 1}
    """
    params.append(limit if limit > 0 else 100000)
    recs = await conn.fetch(sql, *params)
    return [dict(r) for r in recs]


async def existing_hashes(conn, hashes: List[str]) -> set:
    if not hashes:
        return set()
    recs = await conn.fetch("SELECT content_hash FROM course_kb WHERE content_hash = ANY($1)", hashes)
    return {r["content_hash"] for r in recs}


async def insert_variant(conn, row: Dict[str, Any], source: Dict[str, Any], verify: str, verdict: str):
    """把变式写入 course_kb：通过=draft+variant_auto，失败=quarantine，非数值=manual_review。"""
    status = "quarantine" if verify == "fail" else "draft"
    tag = "variant_quarantine" if verify == "fail" else ("variant_manual_review" if verify == "manual" else "variant_auto")
    kps = source.get("knowledge_points")
    if isinstance(kps, str):
        try:
            kps = json.loads(kps)
        except (ValueError, TypeError):
            kps = []
    analysis = row.get("analysis") or ""
    if verdict:
        analysis = f"{analysis}\n[变式校验] {verdict}".strip()
    await conn.execute(
        "INSERT INTO course_kb "
        "(subject, grade, type, stem, options, answer, analysis, difficulty, "
        " knowledge_points, content_hash, tenant_id, source_tag, status, source_province) "
        "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10,$11,$12,$13,$14) "
        "ON CONFLICT (content_hash) DO NOTHING",
        source.get("subject"), source.get("grade"), row.get("type") or source.get("type"),
        row["stem"],
        json.dumps(row.get("options"), ensure_ascii=False) if row.get("options") else None,
        str(row.get("answer")) if row.get("answer") is not None else None,
        analysis, source.get("difficulty") or 3,
        json.dumps(kps or [], ensure_ascii=False),
        row["content_hash"], TENANT_ID, tag, status,
        f"variant_of:{source.get('content_hash', '')[:12]}",
    )
    return status, tag


# ---------------------------------------------------------------- 主流程
async def run(args, transport: Optional[Callable] = None) -> Dict[str, Any]:
    fields_demo = [{"id": "S1", "stem": "已知 f(x)=x²-2x，求 f(3) 的值", "type": "填空",
                    "answer": "3", "options": None, "knowledge_points": ["函数求值"],
                    "analysis": "代入 x=3 得 9-6=3"}]
    if args.dry_run:
        print("[dry-run] GENERATE SYSTEM:\n" + SYSTEM_GENERATE)
        print("[dry-run] GENERATE USER:\n" + build_generate_prompt(fields_demo, args.per_source))
        demo_variant = [{"id": "S1v1", "stem": "已知 g(x)=x²-4x，求 g(5) 的值", "type": "填空", "options": None}]
        print("[dry-run] SOLVE USER:\n" + build_solve_prompt(demo_variant))
        return {"dry_run": True}

    import asyncpg

    dsn = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not dsn:
        raise SystemExit("未配置 DATABASE_URL")

    outcome: Dict[str, Any] = {"sources": 0, "variants_proposed": 0, "auto_pass": 0,
                               "manual_review": 0, "quarantine": 0, "skipped_hash": 0,
                               "invalid": 0, "llm_fail": 0, "inserted": 0}
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            sources = await fetch_source_rows(conn, args.subject, args.limit, args.only_kp)
            outcome["sources"] = len(sources)
            for i in range(0, len(sources), BATCH_SIZE):
                batch = sources[i:i + BATCH_SIZE]
                # LLM-A：生成变式
                try:
                    gen = await _chat_parsed(
                        SYSTEM_GENERATE, build_generate_prompt(batch, args.per_source),
                        "source_id", transport, 120.0)
                except Exception as exc:  # noqa: BLE001
                    outcome["llm_fail"] += 1
                    print(f"[warn] 批 {i // BATCH_SIZE + 1} LLM-A 失败：{exc!r}", flush=True)
                    continue
                src_by_id = {str(s["id"]): s for s in batch}
                # 把变式摊平，分配唯一临时 id 供 LLM-B 盲解
                variants: List[Dict[str, Any]] = []
                for sid, items in gen.items():
                    src = src_by_id.get(sid)
                    if src is None:
                        outcome["invalid"] += 1
                        continue
                    item_list = items if isinstance(items, list) else [items]
                    for item in item_list[:args.per_source]:
                        if not isinstance(item, dict) or not str(item.get("stem") or "").strip():
                            outcome["invalid"] += 1
                            continue
                        vid = f"{sid}v{len(variants) + 1}"
                        variant = {
                            "id": vid, "source_id": sid, "stem": str(item["stem"]).strip(),
                            "options": item.get("options"), "answer": item.get("answer"),
                            "analysis": item.get("analysis"), "change": item.get("change"),
                            "type": src.get("type"),
                        }
                        variant["content_hash"] = content_hash(
                            variant["stem"], variant["type"] or "", str(variant.get("answer") or ""))
                        variants.append(variant)
                if not variants:
                    continue
                outcome["variants_proposed"] += len(variants)

                # 去重：已存在 hash 跳过（一次查询，不在推导式里反复 await）
                already = await existing_hashes(conn, [x["content_hash"] for x in variants])
                fresh = [v for v in variants if v["content_hash"] not in already]
                # 批内同 hash 只留一个
                seen: set = set()
                dedup = []
                for v in fresh:
                    if v["content_hash"] in seen:
                        continue
                    seen.add(v["content_hash"])
                    dedup.append(v)
                outcome["skipped_hash"] += len(variants) - len(dedup)
                if not dedup:
                    continue

                # LLM-B：盲解（绝不带入 A 的答案）
                try:
                    solved = await _chat_parsed(
                        SYSTEM_SOLVE, build_solve_prompt(dedup), "id", transport, 120.0)
                except Exception as exc:  # noqa: BLE001
                    outcome["llm_fail"] += 1
                    print(f"[warn] 批 {i // BATCH_SIZE + 1} LLM-B 失败：{exc!r}", flush=True)
                    continue

                by_id = {v["id"]: v for v in dedup}
                src_by_id = {str(s["id"]): s for s in batch}
                for vid, sol in solved.items():
                    variant = by_id.get(vid)
                    if variant is None:
                        continue
                    src = src_by_id[variant["source_id"]]
                    ans_b = sol.get("answer")
                    gradeable = is_numeric_gradeable(variant.get("answer"), variant.get("options"))
                    same, verdict = answers_equivalent(variant, variant.get("answer"), ans_b)
                    if same and gradeable:
                        verify, out = "pass", "auto_pass"
                    elif not gradeable:
                        verify, out = "manual", "manual_review"
                    else:
                        verify, out = "fail", "quarantine"
                    status, tag = await insert_variant(
                        conn, variant, src, verify,
                        f"A={variant.get('answer')} B={ans_b} 判据={verdict} 可判定={gradeable}")
                    outcome[out] += 1
                    outcome["inserted"] += 1
                    print(f"  [v] {vid} A={variant.get('answer')!r} B={ans_b!r} → {status}/{tag}", flush=True)
    finally:
        await pool.close()
    return outcome


def main() -> None:
    p = argparse.ArgumentParser(description="T-CAP2 变式生成器")
    p.add_argument("--subject", default="数学")
    p.add_argument("--only-kp", default=None, help="只从含该知识点 code 的源题生成")
    p.add_argument("--limit", type=int, default=0, help="源题数上限，0=不限")
    p.add_argument("--per-source", type=int, default=DEFAULT_PER_SOURCE, help="每道源题生成变式数")
    p.add_argument("--pilot", action="store_true", help="小样本（2 源题 ×1 变式）")
    p.add_argument("--dry-run", action="store_true", help="只打印 prompt")
    args = p.parse_args()
    if args.pilot:
        args.limit = 2
        args.per_source = 1
    result = asyncio.run(run(args))
    print("[done] " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
