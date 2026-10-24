#!/usr/bin/env python3
"""T-F1：``student_mastery`` 键对齐回填（旧扁平中文 KP 名 -> DAG code）。

背景（T-B5 实测）：``student_mastery.knowledge_point`` 历史上按**名称**落库，
而 DAG / ZPD / 图谱全部按 **code** 键组织 —— 12 条历史记录仅 1 条 KP 名能
对上 ``kb_knowledge_points.title``，导致掌握度图谱对历史数据恒空。

策略：**分级匹配，宁可留孤儿也不静默误映射**
  L1 标题精确
  L2 归一化后精确（去「的」/空格/标点/全角）
  L3 核心词共现：从名称里提取在 DAG 语料中出现过的词（含同义扩展），
     候选标题须全部命中；命中唯一 -> 高置信
  L4 降级为「含最长核心词」的候选集，按 Dice(2-gram) 相似度取 top1，
     且要求 top1 显著高于 top2（默认 margin >= 0.08）才采信

任一级拿不到唯一/显著答案 -> 进**孤儿清单**并给出 top3 候选建议，交教研人工
对齐（写进 ``mastery_kp_orphans.json``），绝不静默丢弃或瞎猜。

用法（默认 dry-run，不写库）::

    python scripts/fix_mastery_keys.py                      # 预览
    python scripts/fix_mastery_keys.py --apply              # 落库（事务）
    python scripts/fix_mastery_keys.py --apply --backup     # 落库并先导出备份 JSON

落库合并语义：翻译后的 code 若已存在同学生同 code 的行，则**合并计数**
（practice/correct/wrong 相加，last_practiced_at 取较新），删除被合并的旧行。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore

DEFAULT_DSN = os.environ.get(
    "DB_DSN", "postgresql://tes:Tes%40Pg%23106@127.0.0.1:15432/tes_edu"
)

# --------------------------------------------------------------------------- #
# 归一化与同义扩展
# --------------------------------------------------------------------------- #

#: 同义/省略写法：左侧片段在匹配前替换为右侧（用于核心词共现与相似度计算）。
#: 只做「同一概念的简写」替换，不做「概念降级」（如把「焦点与准线」压成「焦点」会
#: 造成语义漂移，宁可判为孤儿交教研）。
SYNONYMS: Dict[str, str] = {
    "求和": "项和",
    "通项公式": "通项",
}

#: 归一化时剔除的字符（「的」是中文标题里最常见的虚词噪声）
_NOISE = re.compile(r"[\s，。、（）()【】\[\]·、：:／/＋+\-—_]")


def norm(s: str) -> str:
    """归一化：全角转半角 + 去噪声字符 + 去「的」+ 小写。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s or "")
    s = _NOISE.sub("", s)
    s = s.replace("的", "")
    return s.strip().lower()


def apply_synonyms(s: str) -> str:
    """把名称里的省略写法展开为语料里的实际用词。"""
    out = s or ""
    for a, b in SYNONYMS.items():
        out = out.replace(a, b)
    return out


def _bigrams(s: str) -> set:
    s = norm(s)
    return {s[i: i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def dice(a: str, b: str) -> float:
    """Dice 系数（2-gram 集合），0~1。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return 2 * len(ga & gb) / (len(ga) + len(gb))


# --------------------------------------------------------------------------- #
# 核心词提取
# --------------------------------------------------------------------------- #

@dataclass
class TokenizeResult:
    tokens: List[str] = field(default_factory=list)
    #: 名称中未被任何核心词覆盖的片段（概念在 DAG 里找不到对应）
    uncovered: List[str] = field(default_factory=list)


def extract_core_tokens(
    name: str, corpus_titles: Sequence[str], min_len: int = 2
) -> TokenizeResult:
    """从扁平名中提取「在 DAG 语料里真实出现过」的核心词（贪心最长匹配）。

    例：``等差数列通项`` + 语料含「等差数列」「通项」-> tokens=['等差数列通项']
    （整名连续出现在「等差数列的通项公式」里，一次覆盖）。

    同时返回**未覆盖片段**：名称里若有连续 >=2 字在 DAG 语料中根本不存在
    （如「抛物线的焦点与**准线**」——DAG 里没有「准线」节点），说明该名称
    含 DAG 未收录的概念，调用方应判为孤儿而非降级猜测。
    """
    res = TokenizeResult()
    n = norm(apply_synonyms(name or ""))
    if not n:
        return res
    norm_corpus = [norm(t or "") for t in corpus_titles if t]
    tokens: List[Tuple[int, str]] = []
    gap_start = 0
    i = 0
    while i < len(n):
        best = ""
        for L in range(min(8, len(n) - i), min_len - 1, -1):
            frag = n[i: i + L]
            if any(frag in t for t in norm_corpus):
                best = frag
                break
        if best:
            if gap_start < i:
                res.uncovered.append(n[gap_start:i])
            tokens.append((i, best))
            i += len(best)
            gap_start = i
        else:
            i += 1
    if gap_start < len(n):
        res.uncovered.append(n[gap_start:])
    # 去被其它 token 完全包含的短词（如 '数列' 被 '等差数列' 包含）
    kept = [t for _, t in tokens]
    res.tokens = [t for t in kept if not any(t != o and t in o for o in kept)]
    # 未被覆盖的片段里，长度 <2 或纯虚词（如「与」）不算「概念缺失」
    res.uncovered = [
        u for u in res.uncovered if len(u) >= 2 and norm(u) not in ("与", "及", "或")
    ]
    return res


# --------------------------------------------------------------------------- #
# 匹配器（纯函数，便于无 DB 单测）
# --------------------------------------------------------------------------- #

@dataclass
class MatchResult:
    name: str
    code: Optional[str] = None
    title: Optional[str] = None
    level: str = ""                      # L1/L2/L3/L4/""（未命中）
    score: float = 0.0
    margin: float = 0.0
    core_tokens: List[str] = field(default_factory=list)
    #: 名称中 DAG 语料未收录的概念片段（非空 -> 判孤儿交教研）
    uncovered: List[str] = field(default_factory=list)
    #: L4 实际用来定位的核心词
    pivot: str = ""
    candidates: List[Tuple[str, str, float]] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return bool(self.code)


def match_kp(
    name: str,
    kp_index: Sequence[Tuple[str, str]],
    *,
    min_margin: float = 0.08,
    min_score: float = 0.30,
) -> MatchResult:
    """把扁平 KP 名匹配到 DAG code。``kp_index``: [(code, title), ...]。"""
    res = MatchResult(name=name)
    if not name or not kp_index:
        return res

    # L1 标题精确
    exact = [c for c, t in kp_index if t == name]
    if len(exact) == 1:
        res.code, res.level, res.score = exact[0], "L1", 1.0
        res.title = dict(kp_index)[exact[0]]
        return res
    if len(exact) > 1:
        res.level, res.candidates = "L1", [(c, dict(kp_index)[c], 1.0) for c in exact]
        return res

    # L2 归一化后精确
    nn = norm(name)
    norm_hits = [(c, t) for c, t in kp_index if norm(t) == nn]
    if len(norm_hits) == 1:
        res.code, res.title, res.level, res.score = norm_hits[0][0], norm_hits[0][1], "L2", 1.0
        return res

    titles = [t for _, t in kp_index]
    tk = extract_core_tokens(name, titles)
    tokens = tk.tokens
    res.core_tokens = tokens
    if not tokens:
        return res
    nt = [(c, t, norm(t)) for c, t in kp_index]

    # 名称含 DAG 未收录的概念（如「准线」）-> 不猜，直接交教研；
    # 但仍按最具区分度的词给出候选建议，方便教研一键裁定。
    if tk.uncovered:
        res.uncovered = tk.uncovered
        pivot = min(tokens, key=lambda t_: sum(1 for _, _, n_ in nt if t_ in n_))
        res.pivot = pivot
        res.candidates = sorted(
            ((c, t, dice(name, t)) for c, t, n_ in nt if pivot in n_),
            key=lambda x: -x[2],
        )[:3]
        return res

    # L3 核心词全部共现
    full = [(c, t) for c, t, n_ in nt if all(tok in n_ for tok in tokens)]
    if len(full) == 1:
        res.code, res.title, res.level, res.score = full[0][0], full[0][1], "L3", 1.0
        return res

    pool = full
    level = "L3"
    if not pool:
        # L4 降级：用**最具区分度**的核心词定位（命中候选最少的那个词），
        # 而不是最长的——「双曲线的渐近线」里「渐近线」比「双曲线」更具体。
        def _hit_count(tok: str) -> int:
            return sum(1 for _, _, n_ in nt if tok in n_)

        pivot = min(tokens, key=_hit_count)
        pool = [(c, t) for c, t, n_ in nt if pivot in n_]
        res.pivot = pivot
        level = "L4"

    scored = sorted(
        ((c, t, dice(name, t)) for c, t in pool), key=lambda x: -x[2]
    )
    res.candidates = scored[:3]
    if not scored:
        return res
    top_code, top_title, top_score = scored[0]
    second = scored[1][2] if len(scored) > 1 else 0.0
    res.margin = top_score - second
    res.score = top_score
    if top_score >= min_score and res.margin >= min_margin:
        res.code, res.title, res.level = top_code, top_title, level
    return res


# --------------------------------------------------------------------------- #
# DB 层
# --------------------------------------------------------------------------- #

def load_kp_index(conn) -> List[Tuple[str, str]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT code, title FROM kb_knowledge_points "
        "WHERE code IS NOT NULL AND title IS NOT NULL AND btrim(title) <> ''"
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def load_pending_rows(conn) -> List[dict]:
    """待修复行：knowledge_point 不在 kb_knowledge_points.code 集合里的。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT m.id, m.student_id, m.tenant_id, m.subject, m.knowledge_point, "
        "       m.practice_count, m.correct_count, m.wrong_count, m.last_practiced_at "
        "FROM student_mastery m "
        "WHERE m.knowledge_point IS NOT NULL "
        "  AND NOT EXISTS (SELECT 1 FROM kb_knowledge_points k WHERE k.code = m.knowledge_point) "
        "ORDER BY m.id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_existing_codes(conn, student_id: str) -> Dict[str, dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT id, knowledge_point, practice_count, correct_count, wrong_count, last_practiced_at "
        "FROM student_mastery WHERE student_id = %s",
        (student_id,),
    )
    cols = [d[0] for d in cur.description]
    return {r[1]: dict(zip(cols, r)) for r in cur.fetchall()}


def apply_fixes(conn, plan: List[Tuple[dict, MatchResult]]) -> Dict[str, int]:
    """事务内落库：改名 / 合并计数 / 删被合并行。返回统计。"""
    stats = {"renamed": 0, "merged": 0, "deleted": 0}
    cur = conn.cursor()
    for row, m in plan:
        sid = row["student_id"]
        existing = load_existing_codes(conn, sid)
        target = existing.get(m.code)
        if target and target["id"] != row["id"]:
            # 合并到已有 code 行
            cur.execute(
                "UPDATE student_mastery SET practice_count=%s, correct_count=%s, "
                "wrong_count=%s, last_practiced_at=%s WHERE id=%s",
                (
                    (target["practice_count"] or 0) + (row["practice_count"] or 0),
                    (target["correct_count"] or 0) + (row["correct_count"] or 0),
                    (target["wrong_count"] or 0) + (row["wrong_count"] or 0),
                    max(
                        target["last_practiced_at"] or datetime.min,
                        row["last_practiced_at"] or datetime.min,
                    ),
                    target["id"],
                ),
            )
            cur.execute("DELETE FROM student_mastery WHERE id=%s", (row["id"],))
            stats["merged"] += 1
            stats["deleted"] += 1
        else:
            cur.execute(
                "UPDATE student_mastery SET knowledge_point=%s WHERE id=%s",
                (m.code, row["id"]),
            )
            stats["renamed"] += 1
    return stats


def export_backup(conn, path: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT * FROM student_mastery ORDER BY id")
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        rows.append(
            {c: (v.isoformat() if isinstance(v, datetime) else v) for c, v in zip(cols, r)}
        )
    payload = {
        "table": "student_mastery",
        "backed_up_at": datetime.utcnow().isoformat(),
        "row_count": len(rows),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return len(rows)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="T-F1 student_mastery 键对齐回填")
    p.add_argument("--dsn", default=DEFAULT_DSN, help="PG DSN（默认走 15432 隧道）")
    p.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    p.add_argument("--backup", help="落库前把全表导出到该 JSON 路径")
    p.add_argument("--orphans", default="mastery_kp_orphans.json", help="孤儿清单输出路径")
    p.add_argument("--min-margin", type=float, default=0.08, help="L4 相似度优势阈值")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 行（0=全部）")
    args = p.parse_args(argv)

    if psycopg2 is None:
        print("需要 psycopg2：pip install psycopg2-binary", file=sys.stderr)
        return 2

    conn = psycopg2.connect(args.dsn)
    try:
        index = load_kp_index(conn)
        rows = load_pending_rows(conn)
        if args.limit:
            rows = rows[: args.limit]
        print(f"DAG 节点 {len(index)} 个；待对齐 mastery 行 {len(rows)} 条")

        plan: List[Tuple[dict, MatchResult]] = []
        orphans: List[dict] = []
        for r in rows:
            m = match_kp(r["knowledge_point"], index, min_margin=args.min_margin)
            if m.matched:
                plan.append((r, m))
                print(
                    f"  [OK {m.level}] {r['knowledge_point']} -> {m.code} ({m.title})"
                    f"  score={m.score:.3f} margin={m.margin:.3f} tokens={m.core_tokens}"
                )
            else:
                orphans.append(
                    {
                        "id": r["id"],
                        "student_id": r["student_id"],
                        "name": r["knowledge_point"],
                        "core_tokens": m.core_tokens,
                        "uncovered_concepts": m.uncovered,
                        "reason": (
                            f"名称含 DAG 未收录概念 {m.uncovered}，需教研补节点或指定归属"
                            if m.uncovered
                            else "候选项区分度不足（相似度优势低于阈值），需人工裁定"
                        ),
                        "top_score": round(m.score, 4),
                        "top_margin": round(m.margin, 4),
                        "suggestions": [
                            {"code": c, "title": t, "score": round(s, 4)}
                            for c, t, s in m.candidates
                        ],
                    }
                )
                sug = " | ".join(f"{c}:{t}({s:.2f})" for c, t, s in m.candidates[:3])
                print(f"  [ORPHAN] {r['knowledge_point']}  候选建议: {sug or '无'}")

        total = len(rows)
        rate = (len(plan) / total * 100) if total else 100.0
        print(
            f"\n可映射 {len(plan)}/{total} = {rate:.1f}%；孤儿 {len(orphans)} 条"
        )

        if orphans:
            with open(args.orphans, "w", encoding="utf-8") as f:
                json.dump(orphans, f, ensure_ascii=False, indent=2)
            print(f"孤儿清单已写 {args.orphans}（交教研人工对齐）")

        if not args.apply:
            print("\n[dry-run] 未写库。确认无误后加 --apply")
            return 0
        if not plan:
            print("无可落库条目")
            return 0

        if args.backup:
            n = export_backup(conn, args.backup)
            print(f"备份 {n} 行 -> {args.backup}")
        stats = apply_fixes(conn, plan)
        conn.commit()
        print(f"已提交：改名 {stats['renamed']}、合并 {stats['merged']}（删旧行 {stats['deleted']}）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
