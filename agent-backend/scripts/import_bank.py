"""批量导入真题 / 采购题库到 106 pgvector（3000 题就绪）。

把教研/采购得到的题库（.csv/.json/.jsonl，或 .xlsx）灌入：
  - course_kb            ：Web 后端规范题库（全字段 + embedding + content_hash 去重）
  - course_kb_embeddings ：Agent 后端 RAG 检索表（与 rag.py 查询一致）

特性：
  - 中英文列名自动映射（题干/答案/解析/知识点/难度/题型/章节/年级/卷别/来源省份）
  - 知识点支持 "；/、|,，" 多种分隔，自动拆成 list
  - 批量 embedding（按 EMBED_BATCH 分批，单批失败指数退避重试，可安全重跑）
  - content_hash 幂等：已存在的题跳过，断点续跑不重复写
  - --dry-run 只解析+映射+校验并打印样例，不连库、不写数据

用法：
    export DB_DSN=postgresql://tes:***@106-host:5432/tes_edu
    export EMBED_BASE_URL=https://xxx/v1  EMBED_API_KEY=sk-xxx  EMBED_MODEL=qwen3.7-text-embedding
    python scripts/import_bank.py bank.csv --subject 数学 --paper 新高考I卷 --source 河南
    python scripts/import_bank.py bank.csv --dry-run        # 先验证列映射
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# 列名别名映射（大小写/空格不敏感）
# ---------------------------------------------------------------------------
ALIASES = {
    "subject": ["学科", "科目", "subject"],
    "grade": ["年级", "学段", "grade"],
    "chapter": ["章节", "单元", "chapter"],
    "type": ["题型", "类型", "type", "qtype"],
    "stem": ["题干", "题目", "试题", "question", "stem", "q"],
    "options": ["选项", "options", "choice", "choices"],
    "answer": ["答案", "作答", "answer", "ans"],
    "analysis": ["解析", "详解", "解答", "analysis", "solution"],
    "difficulty": ["难度", "difficulty", "level"],
    "paper_type": ["卷别", "试卷", "考卷", "paper_type", "paper", "exam"],
    "source_province": ["来源省份", "省份", "来源", "source_province", "source", "province"],
    "knowledge_points": ["知识点", "考点", "knowledge_points", "kp", "points"],
}
_KP_SPLIT = "；;、/|，,"


def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "")


def _build_header_map(headers: list[str]) -> dict[str, str]:
    """把文件表头映射到规范字段。"""
    norm = {_norm_key(h): h for h in headers}
    mapping: dict[str, str] = {}
    for field, aliases in ALIASES.items():
        for a in aliases:
            if _norm_key(a) in norm:
                mapping[field] = norm[_norm_key(a)]
                break
    return mapping


def _split_kp(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    for ch in _KP_SPLIT:
        s = s.replace(ch, "|")
    return [p.strip() for p in s.split("|") if p.strip()]


def _norm_type(v) -> str:
    s = str(v or "").strip()
    if not s:
        return "解答"
    if "选择" in s or "单选" in s or "多选" in s:
        return "选择"
    if "填空" in s:
        return "填空"
    if "压轴" in s:
        return "压轴"
    return "解答"


def _norm_difficulty(v) -> int:
    if v is None or v == "":
        return 3
    s = str(v).strip()
    try:
        n = int(float(s))
    except ValueError:
        if any(k in s for k in ["易", "基础", "简单"]):
            return 1
        if any(k in s for k in ["中", "中等"]):
            return 3
        if any(k in s for k in ["难", "压"]):
            return 5
        return 3
    if n <= 5:
        return max(1, min(5, n))
    # 可能是百分制 0-100
    return max(1, min(5, round(n / 20)))


def _parse_file(content: bytes, filename: str) -> list[dict]:
    name = (filename or "").lower()
    if name.endswith(".json"):
        data = json.loads(content.decode("utf-8"))
        return data.get("questions", [data]) if isinstance(data, dict) else data
    if name.endswith(".jsonl"):
        return [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        return [dict(r) for r in reader]
    if name.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("需要 openpyxl 解析 xlsx，请 `pip install openpyxl` 或先另存为 .csv")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        rows = list(ws.values)
        if not rows:
            return []
        header = [str(h).strip() for h in rows[0]]
        return [dict(zip(header, r)) for r in rows[1:]]
    raise ValueError(f"不支持的文件类型：{filename}（支持 csv/json/jsonl/xlsx）")


def _content_for_embed(row: dict) -> str:
    stem = row.get("stem") or ""
    analysis = row.get("analysis") or ""
    answer = row.get("answer") or ""
    body = stem
    if analysis:
        body += "\n解析：" + analysis
    elif answer:
        body += "\n答案：" + answer
    return body


def _hash(row: dict) -> str:
    raw = f"{row.get('subject','')}|{row.get('stem','')}|{row.get('answer','')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _embed_with_retry(texts: list[str], embed_fn, max_retry: int = 3) -> list[list[float]]:
    last = None
    for attempt in range(max_retry):
        try:
            return await embed_fn(texts)
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 2 ** attempt
            print(f"  [warn] embedding 第 {attempt+1} 次失败：{e}；{wait}s 后重试")
            time.sleep(wait)
    raise last  # type: ignore[misc]


async def _ensure_embeddings_table(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS content_hash text"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_emb_hash "
            "ON course_kb_embeddings(content_hash)"
        )


async def _run(args) -> None:
    # 延迟导入：避免无依赖时 import 失败
    from app.core import config, embeddings  # noqa: E402
    from app.db import pg  # noqa: E402

    if not config.db_ready():
        raise SystemExit("未配置 DB_DSN，无法灌库。")

    raw = open(args.file, "rb").read()
    records = _parse_file(raw, args.file)
    if not records:
        raise SystemExit("文件解析后无数据。")

    header_map = _build_header_map(list(records[0].keys()))
    print(f"检测到列映射：{header_map}")

    # 归一化
    norm: list[dict] = []
    for r in records:
        row = {field: (r.get(src) if src is not None else None) for field, src in header_map.items()}
        # 缺失 subject/paper/source 用命令行默认值补齐
        row["subject"] = (row.get("subject") or args.subject or "数学")
        row["paper_type"] = row.get("paper_type") or args.paper
        row["source_province"] = row.get("source_province") or args.source
        row["grade"] = row.get("grade") or args.grade
        row["chapter"] = row.get("chapter") or None
        row["type"] = _norm_type(row.get("type"))
        row["difficulty"] = _norm_difficulty(row.get("difficulty"))
        kp = _split_kp(row.get("knowledge_points"))
        row["knowledge_points"] = kp
        opts = row.get("options")
        row["options"] = opts if isinstance(opts, list) else None
        row["stem"] = (row.get("stem") or "").strip()
        row["answer"] = (row.get("answer") or "").strip()
        row["analysis"] = (row.get("analysis") or "").strip()
        row["content_hash"] = _hash(row)
        row["content"] = _content_for_embed(row)
        if not row["stem"]:
            continue  # 无题干跳过
        norm.append(row)

    total = len(norm)
    print(f"归一化后有效题目：{total}")

    if args.dry_run:
        for demo in norm[:3]:
            print("  样例 ->", {k: demo[k] for k in ("subject", "type", "difficulty", "stem", "knowledge_points", "paper_type")})
        print("[dry-run] 未写入数据库。")
        return

    # 断点续跑：收集已存在的 content_hash
    pool = await pg.get_pool()
    await _ensure_embeddings_table(pool)
    existing: set[str] = set()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT content_hash FROM course_kb WHERE content_hash = ANY($1)", [r["content_hash"] for r in norm])
        existing.update(r["content_hash"] for r in rows)
        rows2 = await conn.fetch("SELECT content_hash FROM course_kb_embeddings WHERE content_hash = ANY($1)", [r["content_hash"] for r in norm])
        existing.update(r["content_hash"] for r in rows2)
    todo = [r for r in norm if r["content_hash"] not in existing]
    print(f"已存在跳过：{total - len(todo)}，待写入：{len(todo)}")

    if not todo:
        print("没有需要新写入的题目。")
        return

    # 批量 embedding（分批 + 重试）
    texts = [r["content"] for r in todo]
    vectors: list[list[float] | None] = [None] * len(todo)
    batch = max(1, config.EMBED_BATCH) * 10  # 导入场景放大批次，加速
    embed_fail = 0
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        try:
            vs = await _embed_with_retry(chunk, embeddings.embed)
            for j, v in enumerate(vs):
                vectors[i + j] = v
        except Exception as e:  # noqa: BLE001
            embed_fail += len(chunk)
            print(f"  [error] 批次 {i}-{i+len(chunk)} embedding 彻底失败：{e}，该批留空向量")
        if (i // batch) % 5 == 0:
            print(f"  embedding 进度 {min(i+batch, len(texts))}/{len(texts)}")

    inserted = duplicated = failed = 0
    async with pool.acquire() as conn:
        for r, vec in zip(todo, vectors):
            try:
                emb_lit = "[" + ",".join(f"{x:.6f}" for x in vec) + "]" if vec else None
                # 1) 规范库 course_kb
                await conn.execute(
                    """
                    INSERT INTO course_kb
                        (subject, grade, chapter, type, stem, options, answer, analysis,
                         difficulty, paper_type, source_province, knowledge_points, content_hash, embedding)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12::jsonb,$13,$14::vector)
                    ON CONFLICT (content_hash) DO NOTHING
                    """,
                    r["subject"], r["grade"], r["chapter"], r["type"], r["stem"],
                    json.dumps(r["options"] or []), r["answer"], r["analysis"],
                    r["difficulty"], r["paper_type"], r["source_province"],
                    json.dumps(r["knowledge_points"]), r["content_hash"], emb_lit,
                )
                # 2) RAG 检索表 course_kb_embeddings
                await conn.execute(
                    """
                    INSERT INTO course_kb_embeddings
                        (subject, knowledge_point, difficulty, question, answer, content, embedding, content_hash)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::vector,$8)
                    ON CONFLICT (content_hash) DO NOTHING
                    """,
                    r["subject"],
                    r["knowledge_points"][0] if r["knowledge_points"] else "",
                    r["difficulty"], r["stem"], r["answer"], r["content"], emb_lit,
                    r["content_hash"],
                )
                inserted += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                if failed <= 5:
                    print(f"  [fail] {r['stem'][:30]}: {e}")

    print(f"完成：新增 {inserted}，跳过(重复) {duplicated}，失败 {failed}，embedding 失败 {embed_fail}")


def main() -> None:
    ap = argparse.ArgumentParser(description="批量导入题库到 106 pgvector")
    ap.add_argument("file", help="题库文件 .csv/.json/.jsonl/.xlsx")
    ap.add_argument("--subject", default="数学")
    ap.add_argument("--paper", default="新高考I卷")
    ap.add_argument("--source", default=None, help="来源省份，如 河南/河北")
    ap.add_argument("--grade", default=None, help="如 高一/高二/高三")
    ap.add_argument("--dry-run", action="store_true", help="只解析校验，不写库")
    args = ap.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
