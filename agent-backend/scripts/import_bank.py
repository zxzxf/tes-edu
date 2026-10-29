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
import re
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
    "steps": ["步骤", "steps", "子问题", "小问"],
}
_KP_SPLIT = "；;、/|，,"


def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "")


# 选项标签：形如 "A."、"B、"、"C："（支持全角点/顿号/冒号），前面须为行首或分隔符
_OPT_LABEL = re.compile(r"(?:^|[\s;；,，、|])([A-D])\s*[.．、:：]\s*")
# 选择题合法答案：仅 A-D 字母组合（单选 1 个、多选多个）
_ANSWER_OK = re.compile(r"^[A-Da-d]+$")


def _parse_options(v):
    """把选项文本解析成 {A: "...", B: "..."} 结构。

    CSV/Excel 里选项常写成一整串，如 "A.2,−4 B.2,5 C.−2,4 D.−2,5"，
    **选项值本身可能含逗号**，因此绝不能按逗号切分，只能按 "A." 这类标签切。
    此前直接要求 isinstance(list) 会导致 CSV 导入的选择题选项全部丢失成 None。
    """
    if v is None:
        return None
    if isinstance(v, dict):
        out = {str(k).strip()[:8]: str(val)[:500] for k, val in v.items() if str(k).strip()}
        return out or None
    if isinstance(v, list):
        out: dict[str, str] = {}
        for i, item in enumerate(v):
            if isinstance(item, dict):
                lab = str(item.get("label") or item.get("key") or "").strip()[:8]
                val = str(item.get("text") or item.get("value") or "")
                if lab:
                    out[lab] = val[:500]
            else:
                out[chr(ord("A") + i)] = str(item)[:500]
        return out or None
    s = str(v).strip()
    if not s:
        return None
    marks = list(_OPT_LABEL.finditer(s))
    if len(marks) < 2:  # 不足两个标签，无法可靠切分，宁可留空也不猜
        return None
    out = {}
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
        out[m.group(1)] = s[start:end].strip()[:500]
    return out or None


def _validate_row(row: dict) -> str | None:
    """行级业务校验（S1 列数闸门之外的第二道闸）。返回拒收原因，None 表示通过。

    主要拦"假元数据"：选择题答案不是 A-D（如 demo 里的 "图示题演示略"、
    "B演示待核"）——这类占位值一旦入库，判分/组卷/RAG 全会被污染。
    """
    qtype = str(row.get("type") or "")
    ans = str(row.get("answer") or "").strip()
    if qtype in ("单选", "多选", "选择"):
        if not ans:
            return "选择题缺答案"
        if not _ANSWER_OK.match(ans):
            return f"选择题答案非法(应为 A-D)：{ans[:20]!r}"
        if qtype == "单选" and len(ans) != 1:
            return f"单选题答案应为单个字母：{ans[:20]!r}"
    if not str(row.get("stem") or "").strip():
        return "缺题干"
    return None


def _gate_rows(header: list[str], rows: list, start_line: int = 2) -> tuple[list[dict], list[dict]]:
    """列数校验闸门：列数≠表头即拒收该行并报告（S1 验收项）。

    旧实现用 csv.DictReader，列多了会被塞进 restkey、列少了静默补 None，
    **错位数据照样入库**（题干串到答案列是灾难且难以事后发现）。
    """
    ncols = len(header)
    good: list[dict] = []
    rejects: list[dict] = []
    for i, r in enumerate(rows, start=start_line):
        cells = [("" if c is None else str(c)) for c in (r or [])]
        if not any(c.strip() for c in cells):
            continue  # 空行静默跳过，不算拒收
        if len(cells) != ncols:
            rejects.append({
                "line": i,
                "reason": "列数不符",
                "detail": f"该行 {len(cells)} 列 / 表头 {ncols} 列",
                "preview": " | ".join(c[:16] for c in cells[:6]),
            })
            continue
        rec = dict(zip(header, cells))
        # 保留原始行号：被拒收的行不进入 records，若按数组下标推算行号会整体前移
        rec["__line__"] = i
        good.append(rec)
    return good, rejects


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


def _norm_steps(v, row=None) -> list[dict]:
    """校验/兜底步骤列表。没有步骤字段时生成兜底 single 步（全题型覆盖的最后防线）。"""
    if isinstance(v, list) and v:
        out = []
        for st in v:
            if not isinstance(st, dict):
                continue
            out.append({
                "step_kind": str(st.get("step_kind") or "single")[:32],
                "label": str(st.get("label") or "")[:64],
                "text": str(st.get("text") or "")[:2000],
                "is_correct": st.get("is_correct"),
                "kp_code": (str(st.get("kp_code") or "")[:64] or None),
                "kp_name": (str(st.get("kp_name") or "")[:128] or None),
                "kp_source": str(st.get("kp_source") or "question")[:16],
                "confidence": (str(st.get("confidence") or "")[:16] or None),
            })
        if out:
            return out
    # 兜底：一步 single，kp 继承题级第一个知识点
    kps = (row or {}).get("knowledge_points") or []
    kp0 = str(kps[0]) if kps else None
    return [{
        "step_kind": "single", "label": "全题",
        "text": str((row or {}).get("stem") or "")[:300],
        "is_correct": None, "kp_code": kp0, "kp_name": None,
        "kp_source": "question", "confidence": None,
    }]


def _norm_type(v) -> str:
    """题型归一化。

    注意：单选/多选必须分开——组卷要控制题型配比，自适应也要区分难度模型，
    两者混成"选择"会让这两件事都做不了。course_kb.type 为 varchar(16)，无 CHECK 约束。
    """
    s = str(v or "").strip()
    if not s:
        return "解答"
    if "多选" in s:
        return "多选"
    if "单选" in s:
        return "单选"
    if "选择" in s:
        return "选择"      # 来源未区分单选/多选时的兜底
    if "实验" in s:
        return "实验"
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


def _parse_file(content: bytes, filename: str) -> tuple[list[dict], list[dict]]:
    """解析题库文件。

    返回 (records, rejects)；rejects 是被"列数闸门"拒收的行（含行号与原因），
    JSON/JSONL 无列的概念，故恒为空。
    """
    name = (filename or "").lower()
    if name.endswith(".json"):
        data = json.loads(content.decode("utf-8-sig"))
        recs = data.get("questions", [data]) if isinstance(data, dict) else data
        return recs, []
    if name.endswith(".jsonl"):
        return ([json.loads(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()], [])
    if name.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        if not rows:
            return [], []
        header = [str(h).strip() for h in rows[0]]
        return _gate_rows(header, rows[1:])
    if name.endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("需要 openpyxl 解析 xlsx，请 `pip install openpyxl` 或先另存为 .csv")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        rows = list(ws.values)
        if not rows:
            return [], []
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        return _gate_rows(header, rows[1:])
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
        # T-C3 题型路由：type 列（老库补齐；新值随插入带上）
        await conn.execute(
            "ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT ''"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_emb_hash "
            "ON course_kb_embeddings(content_hash)"
        )
        # 老库历史行回填题型（幂等；与 scripts/add_qtype_to_embeddings.sql 同口径）
        await conn.execute(
            "UPDATE course_kb_embeddings e SET type = c.type FROM course_kb c "
            "WHERE e.content_hash = c.content_hash AND (e.type IS NULL OR e.type = '')"
        )


async def _ensure_steps_table(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_kb_steps (
                content_hash text NOT NULL,
                step_no      integer NOT NULL,
                step_kind    text NOT NULL,
                label        text,
                text         text,
                is_correct   boolean,
                kp_code      text,
                kp_name      text,
                kp_source    text NOT NULL DEFAULT 'question',
                confidence   text,
                PRIMARY KEY (content_hash, step_no)
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_steps_kp_code ON course_kb_steps(kp_code)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_steps_kind ON course_kb_steps(step_kind)"
        )


async def _upsert_steps(conn, row) -> int:
    """幂等写步骤：先删该题的旧步骤再插。**独立于 course_kb 存在性检查**，
    这样已灌过的题重跑 import 也能补上/更新步骤（2026-10-23 回灌 213 题靠它）。"""
    await conn.execute(
        "DELETE FROM course_kb_steps WHERE content_hash = $1", row["content_hash"]
    )
    n = 0
    for i, st in enumerate(row["steps"], 1):
        await conn.execute(
            """
            INSERT INTO course_kb_steps
                (content_hash, step_no, step_kind, label, text, is_correct,
                 kp_code, kp_name, kp_source, confidence)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (content_hash, step_no) DO NOTHING
            """,
            row["content_hash"], i, st["step_kind"], st["label"], st["text"],
            st["is_correct"], st["kp_code"], st["kp_name"], st["kp_source"],
            st["confidence"],
        )
        n += 1
    return n


async def _run(args) -> None:
    raw = open(args.file, "rb").read()
    records, rejects = _parse_file(raw, args.file)
    if not records:
        raise SystemExit("文件解析后无数据。")

    header_map = _build_header_map(list(records[0].keys()))
    print(f"检测到列映射：{header_map}")

    # 归一化
    norm: list[dict] = []
    for idx, r in enumerate(records):
        # CSV/XLSX 走闸门时带 __line__（真实行号）；JSON/JSONL 无行概念，退回序号
        lineno = int(r.get("__line__") or (idx + 2))
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
        # 全题型步骤（步骤级归因）；缺失则兜底 single 步
        row["steps"] = _norm_steps(row.get("steps"), row)
        # 选项：CSV/Excel 里是整串文本（值内含逗号），必须结构化，否则选项全丢
        row["options"] = _parse_options(row.get("options"))
        row["stem"] = (row.get("stem") or "").strip()
        row["answer"] = (row.get("answer") or "").strip()
        row["analysis"] = (row.get("analysis") or "").strip()
        # 第二道闸：假元数据（如选择题答案是"图示题演示略"）
        reason = _validate_row(row)
        if reason:
            rejects.append({
                "line": lineno, "reason": reason, "detail": "",
                "preview": row["stem"][:24],
            })
            continue
        # 租户与题源标记（方案书 v1.2/v1.3：course_kb 无 tenant_id 硬缺口，
        # 旧 211 行统一归 school_pilot_01；新导入题 source_tag 区别于 gaokao_real，
        # 便于脏题隔离与组卷按来源优先）。0005 迁移会先补这两列。
        row["tenant_id"] = getattr(args, "tenant", "school_pilot_01")
        row["source_tag"] = getattr(args, "source_tag", "import")
        row["content_hash"] = _hash(row)
        row["content"] = _content_for_embed(row)
        norm.append(row)

    total = len(norm)
    print(f"归一化后有效题目：{total}")

    # 闸门报告：错位行 / 假元数据一律可见，不静默吞掉
    if rejects:
        rejects.sort(key=lambda x: x.get("line") or 0)
        print(f"[闸门] 拒收 {len(rejects)} 行（列数不符或元数据不合规）：")
        for rj in rejects[:10]:
            print(f"  - 第 {rj['line']} 行：{rj['reason']} {rj.get('detail', '')}"
                  f"{(' | ' + rj['preview']) if rj.get('preview') else ''}")
        if len(rejects) > 10:
            print(f"  ... 另有 {len(rejects) - 10} 行，详见上文规则")
        if args.strict:
            raise SystemExit(f"--strict 模式：存在 {len(rejects)} 行不合规，已中止导入（未写库）。")

    if args.dry_run:
        for demo in norm[:3]:
            print("  样例 ->", {k: demo[k] for k in ("subject", "type", "difficulty", "stem", "knowledge_points", "paper_type")})
        kinds = {}
        for r in norm:
            for s in r["steps"]:
                kinds[s["step_kind"]] = kinds.get(s["step_kind"], 0) + 1
        print(f"  [dry-run] 步骤总数 {sum(kinds.values())}，按 kind: {kinds}")
        print("[dry-run] 未写入数据库。")
        return

    # 延迟导入：避免无依赖时 import 失败（dry-run 不需要）
    from app.core import config, embeddings  # noqa: E402
    from app.db import pg  # noqa: E402

    if not config.db_ready():
        raise SystemExit("未配置 DB_DSN，无法灌库。")

    # 断点续跑：收集已存在的 content_hash
    pool = await pg.get_pool()
    await _ensure_embeddings_table(pool)
    await _ensure_steps_table(pool)
    existing: set[str] = set()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT content_hash FROM course_kb WHERE content_hash = ANY($1)", [r["content_hash"] for r in norm])
        existing.update(r["content_hash"] for r in rows)
        rows2 = await conn.fetch("SELECT content_hash FROM course_kb_embeddings WHERE content_hash = ANY($1)", [r["content_hash"] for r in norm])
        existing.update(r["content_hash"] for r in rows2)
        # 步骤回灌独立于存在性跳过：已存在的老题也要补步骤（DELETE+INSERT 幂等）
        steps_written = 0
        async with conn.transaction():
            for r in norm:
                steps_written += await _upsert_steps(conn, r)
    print(f"步骤回灌完成：{len(norm)} 题 / {steps_written} 步（含已存在题）")

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
                         difficulty, paper_type, source_province, knowledge_points, content_hash, embedding,
                         tenant_id, source_tag)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11,$12::jsonb,$13,$14::vector,$15,$16)
                    ON CONFLICT (content_hash) DO NOTHING
                    """,
                    r["subject"], r["grade"], r["chapter"], r["type"], r["stem"],
                    json.dumps(r["options"] or []), r["answer"], r["analysis"],
                    r["difficulty"], r["paper_type"], r["source_province"],
                    json.dumps(r["knowledge_points"]), r["content_hash"], emb_lit,
                    r["tenant_id"], r["source_tag"],
                )
                # 2) RAG 检索表 course_kb_embeddings
                await conn.execute(
                    """
                    INSERT INTO course_kb_embeddings
                        (subject, knowledge_point, difficulty, question, answer, content, embedding, content_hash, type)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::vector,$8,$9)
                    ON CONFLICT (content_hash) DO NOTHING
                    """,
                    r["subject"],
                    r["knowledge_points"][0] if r["knowledge_points"] else "",
                    # course_kb.difficulty 是 integer，但 course_kb_embeddings.difficulty 是 text；
                    # _norm_difficulty 返回 int，直接传会让第二张表报 expected str, got int
                    str(r["difficulty"]), r["stem"], r["answer"], r["content"], emb_lit,
                    r["content_hash"],
                    # T-C3 题型路由：题型随检索 chunks 带回（qtype.route_question_tier）
                    r["type"],
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
    ap.add_argument("--tenant", default="school_pilot_01",
                    help="题目归属租户（试点校默认 school_pilot_01）")
    ap.add_argument("--source-tag", dest="source_tag", default="import",
                    help="题源标记，如 gaokao_real / import / teacher")
    ap.add_argument("--strict", action="store_true",
                    help="只要闸门拒收了任何一行就整体中止（默认仅拒收并报告）")
    args = ap.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
