"""T-CAP4 入库自动校验管线（L5 质量闸门）。

新题入库即跑三闸，失败自动 quarantine，人只处理异常队列：

    闸1 去重      content_hash 精确 + 文本归一化指纹近似（抓 OCR/标点差异的同题异 hash）
    闸2 答案自洽  复用 core/answer_verify 机械代入 + core/equiv 答案等价；缺答案/选项越界=明确错误
    闸3 双源      仅当存在真正独立的第二来源（如卷面快照 vs 题库现行）才比对；无来源不误判

裁定口径（红线：不误伤正常题）
--------------------------------
* **quarantine**：明确错误——重复(conflict/duplicate) / 答案缺失 / 选项越界 /
  机械代入明确失败（非参数题）。落 ``status='quarantine'`` + ``source_tag='guard_quarantine'``，
  被组卷/检索自动排除（复用 M0-2 隔离），人只在异常队列处理。
* **manual_review**：无任何自动依据（选择题无第二来源、解答题非数值、代入不适用）——
  **不隔离**，落 ``status='draft'`` + ``source_tag='guard_manual_review'``，仅打标提醒人工看。
  这是 T-CAP4 与「一切从严」的分界：无法自动判定 ≠ 错误。
* **pass**：至少一闸给出自动通过依据（双源一致 / 机械代入通过）→ 保持原状态不动。

交付形态（用户 2026-11-17 决策）
--------------------------------
独立运维脚本 + 可复用纯函数（``judge_item``），**不 hook 三条运行时入库链路**。
先对存量 draft 题补跑闸门、跑通 quarantine 分流与异常队列，再决定是否 hook 主链路。
默认只扫 ``status='draft'``（published 池是组卷来源，显式 --include-published 才纳入）。

用法（在 edu-web-backend 容器内，cwd=/app）
------------------------------------------
    # 纯函数自检（不连库）
    python scripts/intake_guard.py --self-test

    # dry-run：只读统计三闸命中分布，不写库
    python scripts/intake_guard.py --dry-run

    # 真跑：把判罚写回 status/source_tag（可 --limit 小样本）
    python scripts/intake_guard.py --apply --limit 20

    # 纳入 published 一起审（谨慎，会影响组卷池）
    python scripts/intake_guard.py --apply --include-published

环境变量：DATABASE_URL（容器内已注入）。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import equiv as eq  # noqa: E402
from app.core import answer_verify as av  # noqa: E402
from app.core.exam_types import to_qtype  # noqa: E402

VERDICT_PASS = "pass"
VERDICT_MANUAL = "manual_review"
VERDICT_QUARANTINE = "quarantine"

TAG_QUARANTINE = "guard_quarantine"
TAG_MANUAL = "guard_manual_review"


# ---------------------------------------------------------------- 闸1：去重
def text_fingerprint(stem: Any) -> Optional[str]:
    """题干文本指纹：norm_text 归一后剥掉所有标点/空白，只留字母数字。

    与 content_hash 互补：content_hash=sha256(stem|type|answer) 对标点/全角差异敏感
    （T-CAP1 实证 sid=3/61 同题异 hash）。本指纹抹平这些格式差异，专门抓
    「题干实质相同、指纹不同」的重复题。数字保留——数字变了就是不同题（变式），不判重。
    """
    s = eq.norm_text(str(stem or ""))
    s = "".join(ch for ch in s if ch.isalnum())
    return hashlib.sha1(s.encode("utf-8")).hexdigest() if s else None


def dedup_check(
    row: Dict[str, Any],
    *,
    known_hashes: Set[str],
    known_fps: Set[str],
) -> Optional[Dict[str, str]]:
    """命中重复返回 {"gate","reason"}，否则 None。known_* 不含题自身。"""
    ch = str(row.get("content_hash") or "").strip()
    if ch and ch in known_hashes:
        return {"verdict": VERDICT_QUARANTINE, "gate": "dedup_exact",
                "reason": f"content_hash 精确重复：{ch[:12]}"}
    fp = text_fingerprint(row.get("stem"))
    if fp and fp in known_fps:
        return {"verdict": VERDICT_QUARANTINE, "gate": "dedup_text",
                "reason": f"题干文本指纹重复（OCR/标点差异）：{fp[:12]}"}
    return None


# ---------------------------------------------------------------- 闸2：自洽
def _internal_qtype(raw: Any) -> str:
    """中文题型（填空/选择/解答）→ 内部题型（blank/choice/subjective）。

    core/answer_verify 的 mechanical_check 只认内部题型，直接传中文会被判 NA。
    """
    parsed = to_qtype(str(raw or "").strip())
    if parsed:
        return parsed[0]
    return str(raw or "").strip().lower()


def _option_labels(options: Any) -> Set[str]:
    """从选项列表/dict 提取选项标签集合（选择题答案越界检查用）。"""
    labels: Set[str] = set()
    if isinstance(options, dict):
        for k in options:
            labels.add(str(k).strip().upper())
    elif isinstance(options, (list, tuple)):
        for opt in options:
            m = re.match(r"^\s*([A-Ha-h])\s*[\.、\):：,，]", str(opt))
            if m:
                labels.add(m.group(1).upper())
    return labels


def consistency_check(row: Dict[str, Any]) -> Dict[str, Any]:
    """答案自洽闸。返回 {"verdict","gate","reason"}。

    verdict 取值：pass（机械代入通过）/ quarantine（明确错误）/ manual_review（无法自动判定）。
    """
    answer = str(row.get("answer") or "").strip()
    if not answer:
        return {"verdict": VERDICT_QUARANTINE, "gate": "missing_answer",
                "reason": "答案为空，无法进入判分/组卷"}

    qtype = _internal_qtype(row.get("type"))
    options = row.get("options")
    labels = _option_labels(options)

    # 选择题：答案字母越界 = 明确错误（不是"无法判定"）
    if labels and re.fullmatch(r"[A-Ha-h]+", answer):
        bad = [c for c in answer.upper() if c not in labels]
        if bad:
            return {"verdict": VERDICT_QUARANTINE, "gate": "answer_out_of_range",
                    "reason": f"答案 {answer.upper()} 含越界选项 {''.join(bad)}，实际选项 {sorted(labels)}"}
        # 答案在选项内且非多选自洽可验：格式正确但内容对错无法机器判 → manual
        return {"verdict": VERDICT_MANUAL, "gate": "choice_no_second_source",
                "reason": "选择题答案格式合法，但无第二来源/非数值，无法自动判对错"}

    # 填空单变量等式：机械代入是唯一能自动证明自洽的手段
    mech = av.mechanical_check(
        qtype, answer, options=options, stem=str(row.get("stem") or ""),
    )
    if mech.get("applicable") and mech.get("passed") is True:
        return {"verdict": VERDICT_PASS, "gate": "mechanical_pass",
                "reason": mech.get("detail") or "机械代入通过"}
    if mech.get("applicable") and mech.get("passed") is False:
        # 代入失败：题干可能含参数（红线：不报假冲突）。转人工，不隔离。
        return {"verdict": VERDICT_MANUAL, "gate": "mechanical_suspect",
                "reason": (mech.get("detail") or "代入未通过") + "（可能为含参题，不硬隔离）"}

    # 数值答案但无等式可代入：等价性无参照，无法证明自洽 → manual
    if eq.to_number(answer) is not None or eq.normalize_interval(answer) is not None:
        return {"verdict": VERDICT_MANUAL, "gate": "numeric_no_reference",
                "reason": "数值答案但题干无可代入等式，缺少自洽参照"}
    return {"verdict": VERDICT_MANUAL, "gate": "non_numeric",
            "reason": "非数值/解答题，equiv 无法确定性判定，转人工复核"}


# ---------------------------------------------------------------- 闸3：双源
def dual_source_check(
    row: Dict[str, Any],
    *,
    secondary_answer: Optional[str] = None,
    secondary_source: str = "kb_snapshot",
) -> Dict[str, Any]:
    """双源闸。仅当调用方传入**真正独立**的第二来源才比对；无来源 → manual。

    第二来源示例：卷面快照 answer vs 题库现行 answer（不同时点/写入方）。
    同源（刚从题库复制的快照）不算独立来源——调用方负责判定，本函数如实比对。
    """
    if secondary_answer is None or not str(secondary_answer).strip():
        return {"verdict": VERDICT_MANUAL, "gate": "no_second_source",
                "reason": "无独立第二来源，G1 无法形成验证"}
    qtype = _internal_qtype(row.get("type"))
    ok = av.answer_equivalent(qtype, str(row.get("answer") or ""), str(secondary_answer))
    if ok:
        return {"verdict": VERDICT_PASS, "gate": "dual_verified",
                "reason": f"双源一致（{secondary_source}）"}
    return {"verdict": VERDICT_QUARANTINE, "gate": "dual_conflict",
            "reason": f"双源冲突：题库「{row.get('answer')}」≠ {secondary_source}「{secondary_answer}」"}


# ---------------------------------------------------------------- 总裁定
def judge_item(
    row: Dict[str, Any],
    *,
    known_hashes: Set[str],
    known_fps: Set[str],
    secondary_answer: Optional[str] = None,
) -> Dict[str, Any]:
    """三闸合并裁定单题。返回 {verdict, gate, reason, checks}。

    优先级：quarantine > pass > manual_review。
    - 任一闸明确错误 → quarantine（隔离）
    - 否则任一闸自动通过 → pass（不动）
    - 否则 → manual_review（打标不隔离）
    """
    checks: List[Dict[str, Any]] = []

    dup = dedup_check(row, known_hashes=known_hashes, known_fps=known_fps)
    checks.append(dup or {"gate": "dedup_ok", "reason": "无重复"})
    cons = consistency_check(row)
    checks.append(cons)
    dual = dual_source_check(row, secondary_answer=secondary_answer)
    checks.append(dual)

    verdicts = [c.get("verdict") for c in checks if "verdict" in c]
    if VERDICT_QUARANTINE in verdicts:
        first = next(c for c in checks if c.get("verdict") == VERDICT_QUARANTINE)
        return {"verdict": VERDICT_QUARANTINE, "gate": first["gate"],
                "reason": first["reason"], "checks": checks}
    if VERDICT_PASS in verdicts:
        first = next(c for c in checks if c.get("verdict") == VERDICT_PASS)
        return {"verdict": VERDICT_PASS, "gate": first["gate"],
                "reason": first["reason"], "checks": checks}
    first = next(c for c in checks if c.get("verdict") == VERDICT_MANUAL)
    return {"verdict": VERDICT_MANUAL, "gate": first["gate"],
            "reason": first["reason"], "checks": checks}


def verdict_to_db(verdict: str, current_status: str = "draft") -> Dict[str, str]:
    """裁定 → 写库字段。pass 不改状态；manual 只在 draft 上打标；quarantine 隔离。"""
    if verdict == VERDICT_QUARANTINE:
        return {"status": "quarantine", "source_tag": TAG_QUARANTINE}
    if verdict == VERDICT_MANUAL:
        # 已 quarantine/published 的不回退；只给 draft 题打标
        if current_status == "draft":
            return {"status": "draft", "source_tag": TAG_MANUAL}
        return {"status": current_status, "source_tag": f"{current_status}_untouched"}
    return {"status": current_status, "source_tag": "unchanged"}


# ---------------------------------------------------------------- DB 主流程
async def _load_rows(conn, *, include_published: bool) -> List[Dict[str, Any]]:
    if include_published:
        sql = (
            "SELECT id, content_hash, stem, type, answer, options, status, source_tag "
            "FROM course_kb ORDER BY id"
        )
        return [dict(r) for r in await conn.fetch(sql)]
    sql = (
        "SELECT id, content_hash, stem, type, answer, options, status, source_tag "
        "FROM course_kb WHERE status = 'draft' ORDER BY id"
    )
    return [dict(r) for r in await conn.fetch(sql)]


async def run(*, apply: bool, include_published: bool, limit: int) -> Dict[str, Any]:
    import asyncpg
    url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not url:
        raise RuntimeError("DATABASE_URL 未设置")
    conn = await asyncpg.connect(url)
    try:
        rows = await _load_rows(conn, include_published=include_published)
        if limit > 0:
            rows = rows[:limit]
        # 全库倒排索引：hash/指纹 → 占用它的题 id 集合。
        # 不能用「排除整批 draft」的做法——那会让批内重复（含 T-CAP1 发现的
        # 同题异 hash）完全检测不到。逐题判断时只排除自身 id 即可。
        all_rows = [dict(r) for r in await conn.fetch(
            "SELECT id, content_hash, stem FROM course_kb"
        )]
        hash_index: Dict[str, Set[int]] = {}
        fp_index: Dict[str, Set[int]] = {}
        for r in all_rows:
            ch = str(r.get("content_hash") or "").strip()
            if ch:
                hash_index.setdefault(ch, set()).add(r["id"])
            fp = text_fingerprint(r.get("stem"))
            if fp:
                fp_index.setdefault(fp, set()).add(r["id"])

        stats: Dict[str, Any] = {
            "scanned": len(rows), "pass": 0, "manual_review": 0,
            "quarantine": 0, "by_gate": {}, "samples": [],
        }
        for r in rows:
            rid = r["id"]
            ch = str(r.get("content_hash") or "").strip()
            fp = text_fingerprint(r.get("stem"))
            # 只有被「其他题」占用的 hash/指纹才算重复（排除自身）
            known_hashes = ({ch} if any(i != rid for i in hash_index.get(ch, set())) else set())
            known_fps = ({fp} if fp and any(i != rid for i in fp_index.get(fp, set())) else set())
            result = judge_item(r, known_hashes=known_hashes, known_fps=known_fps)
            v = result["verdict"]
            stats[v] += 1
            g = result["gate"]
            stats["by_gate"][g] = stats["by_gate"].get(g, 0) + 1
            if len(stats["samples"]) < 12 and v != VERDICT_PASS:
                stats["samples"].append({
                    "id": r["id"], "verdict": v, "gate": g,
                    "reason": result["reason"], "stem": str(r["stem"])[:60],
                })
            if apply:
                target = verdict_to_db(v, str(r.get("status") or "draft"))
                if target["source_tag"] != "unchanged":
                    await conn.execute(
                        "UPDATE course_kb SET status = $1, source_tag = $2 WHERE id = $3",
                        target["status"], target["source_tag"], r["id"],
                    )
        return stats
    finally:
        await conn.close()


# ---------------------------------------------------------------- 自检
def self_test() -> None:
    cases = [
        ("空答案隔离", {"stem": "x=1 求x", "type": "填空", "answer": "",
                        "content_hash": "h1"}, set(), set(), VERDICT_QUARANTINE),
        ("hash精确重复", {"stem": "题干甲", "type": "填空", "answer": "1",
                          "content_hash": "dupe"}, {"dupe"}, set(), VERDICT_QUARANTINE),
        ("文本指纹重复", {"stem": "下列函数中，在区间(0,1)上单调递增的是",
                          "type": "选择", "answer": "B", "content_hash": "h2"},
         set(), {"a" * 40}, VERDICT_QUARANTINE),  # fp 不匹配 → 走 cons → manual；下面单独验 fp
        ("选择题答案越界", {"stem": "选正确项", "type": "选择", "answer": "H",
                            "options": ["A.1", "B.2"], "content_hash": "h3"},
         set(), set(), VERDICT_QUARANTINE),
        ("选择题无来源→manual", {"stem": "选正确项", "type": "选择", "answer": "B",
                                  "options": ["A.1", "B.2"], "content_hash": "h4"},
         set(), set(), VERDICT_MANUAL),
        ("机械代入通过", {"stem": "解方程 x^2-2x-3=0，求x", "type": "填空", "answer": "3",
                          "content_hash": "h5"}, set(), set(), VERDICT_PASS),
        ("双源一致", {"stem": "已知a=2求a+1", "type": "填空", "answer": "3",
                      "content_hash": "h6"}, set(), set(), VERDICT_PASS),
        ("双源冲突", {"stem": "已知a=2求a+1", "type": "填空", "answer": "3",
                      "content_hash": "h7"}, set(), set(), VERDICT_QUARANTINE),
    ]
    for name, row, kh, kf, expect in cases:
        if name == "文本指纹重复":
            kf = {text_fingerprint(row["stem"])}
        secondary = "3" if name == "双源一致" else ("4" if name == "双源冲突" else None)
        got = judge_item(row, known_hashes=kh, known_fps=kf, secondary_answer=secondary)
        flag = "OK" if got["verdict"] == expect else "FAIL"
        print(f"[{flag}] {name}: {got['verdict']} / {got['gate']}")
        if flag == "FAIL":
            raise AssertionError(f"{name}: expect {expect}, got {got['verdict']}")

    # 指纹归一：标点/全角差异应同指纹，数字变化应不同指纹
    a = text_fingerprint("在区间(0,1)上，函数f(x)递增")
    b = text_fingerprint("在区间（0，1）上，函数 f（x） 递增")
    c = text_fingerprint("在区间(0,2)上，函数f(x)递增")
    assert a == b, "标点/全角差异未归一"
    assert a != c, "数字变化不应判同指纹"
    print(f"[OK] 文本指纹归一：格式差异同指纹、数字差异异指纹")
    print(f"\n自检全部通过（{len(cases) + 1} 项）")


def main() -> None:
    ap = argparse.ArgumentParser(description="T-CAP4 入库自动校验管线")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--self-test", action="store_true", help="纯函数自检，不连库")
    g.add_argument("--dry-run", action="store_true", help="只读统计，不写库")
    g.add_argument("--apply", action="store_true", help="真跑写回 status/source_tag")
    ap.add_argument("--include-published", action="store_true", help="纳入 published 题（谨慎）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 题（0=全部）")
    args = ap.parse_args()

    if args.self_test or not (args.dry_run or args.apply):
        self_test()
        return
    stats = asyncio.run(run(
        apply=args.apply, include_published=args.include_published, limit=args.limit,
    ))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
