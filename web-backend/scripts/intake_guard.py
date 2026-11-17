"""T-CAP4 入库自动校验管线（L5 质量闸门）——运维脚本入口。

纯函数已下沉到 ``app/core/intake_guard.py``（供采集链路 hook 复用），本脚本只保留
对**存量题**补跑闸门的 DB 主流程 + 自检 + CLI。

    闸1 去重      content_hash 精确 + 文本归一化指纹近似（抓 OCR/标点差异的同题异 hash）
                  + `--semantic` 时加 embedding 语义近似（pgvector TopK + 数值/标答双过滤）
    闸2 答案自洽  复用 core/answer_verify 机械代入 + core/equiv；缺答案/选项越界=明确错误
    闸3 双源      仅当存在真正独立的第二来源才比对；无来源不误判

裁定口径（红线：不误伤正常题）
--------------------------------
* quarantine：明确错误 → status='quarantine' + source_tag='guard_quarantine'，被组卷排除。
* manual_review：无自动依据 → status='draft' + source_tag='guard_manual_review' 打标不隔离。
* pass：至少一闸自动通过 → 保持原状态。

用法（在 edu-web-backend 容器内，cwd=/app）
------------------------------------------
    python scripts/intake_guard.py --self-test          # 纯函数自检，不连库
    python scripts/intake_guard.py --dry-run            # 只读统计，不写库
    python scripts/intake_guard.py --apply --limit 20   # 真跑写回 status/source_tag
    python scripts/intake_guard.py --apply --include-published  # 纳入 published（谨慎）
    python scripts/intake_guard.py --dry-run --semantic  # 加跑语义近似（只读，需 embedding 端点）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.intake_guard import (  # noqa: E402
    GATE_SEMANTIC, VERDICT_PASS, VERDICT_MANUAL, VERDICT_QUARANTINE,
    text_fingerprint, judge_item, verdict_to_db, verdict_to_fields,
)
from app.services import semantic_dedup  # noqa: E402


# ---------------------------------------------------------------- DB 主流程
async def _load_rows(
    conn, *, include_published: bool, semantic: bool = False,
) -> List[Dict[str, Any]]:
    cols = "id, content_hash, stem, type, answer, options, status, source_tag"
    if semantic:
        cols += ", analysis"
    if include_published:
        return [dict(r) for r in await conn.fetch(
            f"SELECT {cols} FROM course_kb ORDER BY id"
        )]
    return [dict(r) for r in await conn.fetch(
        f"SELECT {cols} FROM course_kb WHERE status = 'draft' ORDER BY id"
    )]


async def run(
    *, apply: bool, include_published: bool, limit: int, semantic: bool = False,
) -> Dict[str, Any]:
    import asyncpg
    url = os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")
    if not url:
        raise RuntimeError("DATABASE_URL 未设置")
    conn = await asyncpg.connect(url)
    try:
        rows = await _load_rows(
            conn, include_published=include_published, semantic=semantic,
        )
        if limit > 0:
            rows = rows[:limit]
        sem_map: Dict[int, Dict[str, Any]] = {}
        if semantic:
            vectors = await semantic_dedup.embed_rows(rows)
            sem_map = await semantic_dedup.collect_semantic_matches_asyncpg(
                conn, rows, vectors=vectors,
            )
        # 全库倒排索引：hash/指纹 → 占用它的题 id 集合。
        # 不能用「排除整批 draft」的做法——那会让批内重复检测不到。逐题只排除自身。
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
            known_hashes = ({ch} if any(i != rid for i in hash_index.get(ch, set())) else set())
            known_fps = ({fp} if fp and any(i != rid for i in fp_index.get(fp, set())) else set())
            result = judge_item(r, known_hashes=known_hashes, known_fps=known_fps)
            hit = sem_map.get(rid)
            if hit and result["verdict"] != VERDICT_QUARANTINE:
                # 语义命中只打标、不隔离；明确错误仍以 quarantine 优先
                result = {**hit, "checks": result.get("checks", [])}
            v = result["verdict"]
            stats[v] += 1
            g = result["gate"]
            stats["by_gate"][g] = stats["by_gate"].get(g, 0) + 1
            if len(stats["samples"]) < 12 and v != VERDICT_PASS:
                stats["samples"].append({
                    "id": rid, "verdict": v, "gate": g,
                    "reason": result["reason"], "stem": str(r["stem"])[:60],
                })
            if apply:
                if result["gate"] == GATE_SEMANTIC:
                    # 语义打标要落 guard_* 三列（含 dup_of），否则异常队列指不出重复对象
                    base = str(r.get("source_tag") or "").split(":", 1)[0] or "manual"
                    fields = verdict_to_fields(
                        v, base_tag=base, gate=g,
                        reason=result["reason"], dup_of=result.get("dup_of"),
                    )
                    await conn.execute(
                        "UPDATE course_kb SET status=$1, source_tag=$2, guard_gate=$3, "
                        "guard_reason=$4, guard_dup_of=$5 WHERE id=$6",
                        fields["status"], fields["source_tag"], fields["guard_gate"],
                        fields["guard_reason"], fields.get("guard_dup_of"), rid,
                    )
                else:
                    target = verdict_to_db(v, str(r.get("status") or "draft"))
                    if target["source_tag"] != "unchanged":
                        await conn.execute(
                            "UPDATE course_kb SET status = $1, source_tag = $2 WHERE id = $3",
                            target["status"], target["source_tag"], rid,
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
         set(), set(), VERDICT_QUARANTINE),
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
    a = text_fingerprint("在区间(0,1)上，函数f(x)递增")
    b = text_fingerprint("在区间（0，1）上，函数 f（x） 递增")
    c = text_fingerprint("在区间(0,2)上，函数f(x)递增")
    assert a == b and a != c
    print("[OK] 文本指纹归一：格式差异同指纹、数字差异异指纹")
    print(f"\n自检全部通过（{len(cases) + 1} 项）")


def main() -> None:
    ap = argparse.ArgumentParser(description="T-CAP4 入库自动校验管线")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--self-test", action="store_true", help="纯函数自检，不连库")
    g.add_argument("--dry-run", action="store_true", help="只读统计，不写库")
    g.add_argument("--apply", action="store_true", help="真跑写回 status/source_tag")
    ap.add_argument("--include-published", action="store_true", help="纳入 published 题（谨慎）")
    ap.add_argument("--semantic", action="store_true", help="加跑 embedding 语义近似（第一闸补盲）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 题（0=全部）")
    args = ap.parse_args()

    if args.self_test or not (args.dry_run or args.apply):
        self_test()
        return
    stats = asyncio.run(run(
        apply=args.apply, include_published=args.include_published,
        limit=args.limit, semantic=args.semantic,
    ))
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
