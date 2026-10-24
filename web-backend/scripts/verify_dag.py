"""知识点 DAG 验收脚本（只读，不修改数据）。

背景：P3「图谱初始化」的 DoD 由 Apache AGE 改为关系表实现——
  - kb_knowledge_points   知识点节点（code 唯一、importance∈{core,std}、book/chapter/section）
  - kb_kp_prerequisites   前置边（kp_code, prereq_code 复合主键，relation_type 默认 prerequisite）

本脚本把「DAG 是否健康」抽成可重复运行的验收，覆盖四件事：
  1. 覆盖度 coverage ：每个节点必填字段（code/title/subject）非空、code 唯一。
  2. 悬空边 dangling ：每条边的 kp_code / prereq_code 都指向真实存在的节点。
  3. 无环   acyclic  ：Kahn 拓扑排序能覆盖全部节点（否则存在环）。
  4. 核心标记 core    ：至少存在 importance='core' 的高频考点节点。

纯函数（check_* / build_dag_report）不连库，便于无 DB 单元测试；
verify_from_db() 只读取 106，跑完即退出（exit 0 通过 / 1 失败）。

用法：
    # 连真实库（需 DB_DSN 环境变量或 --dsn）
    python scripts/verify_dag.py
    python scripts/verify_dag.py --json          # 机器可读
    python scripts/verify_dag.py --dsn "postgresql://..."  # 覆盖环境变量
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import deque
from typing import Any, Dict, Iterable, List, Sequence, Tuple

# 节点必填字段（缺失即视为不合格，计入覆盖度失败）
REQUIRED_NODE_FIELDS = ("code", "title", "subject")
# importance 合法取值
VALID_IMPORTANCE = {"core", "std", ""}


# --------------------------------------------------------------------------- #
# 纯函数层（不连库，便于测试）
# --------------------------------------------------------------------------- #
def _val(obj: Any, key: str) -> Any:
    """统一取值：dict 用 .get；asyncpg Record / 其它用 []（Record 不支持属性访问）。"""
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, None)


def _code_of(node: Any) -> str:
    return (str(_val(node, "code") or "")).strip()


def _field(node: Any, name: str) -> str:
    v = _val(node, name)
    return "" if v is None else str(v).strip()


def check_coverage(nodes: Sequence[Any]) -> Dict[str, Any]:
    """每个节点必填字段非空，且 code 全局唯一。"""
    missing: List[Dict[str, str]] = []
    seen_codes: Dict[str, int] = {}
    dup_codes: List[str] = []

    for i, n in enumerate(nodes):
        code = _code_of(n)
        if not code:
            missing.append({"index": str(i), "reason": "code 为空", "code": ""})
            continue
        seen_codes[code] = seen_codes.get(code, 0) + 1
        for f in REQUIRED_NODE_FIELDS:
            if not _field(n, f):
                missing.append(
                    {"index": str(i), "reason": f"{f} 为空", "code": code}
                )
    dup_codes = [c for c, cnt in seen_codes.items() if cnt > 1]

    ok = (len(missing) == 0) and (len(dup_codes) == 0)
    return {
        "ok": ok,
        "total": len(nodes),
        "missing_fields": missing,
        "duplicate_codes": dup_codes,
    }


def check_dangling(nodes: Sequence[Any], edges: Sequence[Any]) -> Dict[str, Any]:
    """边引用的 code 必须都存在；同时统计边指向非法 importance 的情况（仅告警）。"""
    codes = {_code_of(n) for n in nodes if _code_of(n)}
    bad: List[Dict[str, str]] = []
    for e in edges:
        kp = _as_str(e, "kp_code")
        pr = _as_str(e, "prereq_code")
        if kp not in codes:
            bad.append({"edge": f"{kp}->{pr}", "reason": f"kp_code 不存在: {kp}"})
        if pr not in codes:
            bad.append({"edge": f"{kp}->{pr}", "reason": f"prereq_code 不存在: {pr}"})
    # 去重（同一条边可能两端都悬空）
    uniq = {b["edge"] + "|" + b["reason"]: b for b in bad}
    return {"ok": len(uniq) == 0, "dangling": list(uniq.values()), "total_edges": len(edges)}


def _as_str(e: Any, key: str) -> str:
    v = _val(e, key)
    return "" if v is None else str(v).strip()


def check_acyclic(nodes: Sequence[Any], edges: Sequence[Any]) -> Dict[str, Any]:
    """Kahn 拓扑排序。覆盖全部节点 => 无环。"""
    indeg: Dict[str, int] = {_code_of(n): 0 for n in nodes if _code_of(n)}
    adj: Dict[str, List[str]] = {c: [] for c in indeg}
    for e in edges:
        kp, pr = _as_str(e, "kp_code"), _as_str(e, "prereq_code")
        # 仅统计两端都在节点集合内的边，悬空边由 check_dangling 负责
        if kp in indeg and pr in adj:
            indeg[kp] += 1
            adj[pr].append(kp)

    q = deque([c for c, d in indeg.items() if d == 0])
    order: List[str] = []
    while q:
        cur = q.popleft()
        order.append(cur)
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)

    total = len(indeg)
    is_dag = len(order) == total
    cycle: List[str] = [c for c, d in indeg.items() if d > 0] if not is_dag else []
    return {
        "ok": is_dag,
        "topo_covered": len(order),
        "total_nodes": total,
        "cycle_nodes": cycle,
    }


def check_core(nodes: Sequence[Any]) -> Dict[str, Any]:
    """至少应有高频考点(core)节点。"""
    core = sum(1 for n in nodes if _field(n, "importance") == "core")
    by_book: Dict[str, int] = {}
    for n in nodes:
        b = _field(n, "book") or "(空)"
        by_book[b] = by_book.get(b, 0) + (1 if _field(n, "importance") == "core" else 0)
    return {"ok": core > 0, "core_count": core, "core_by_book": by_book}


def build_dag_report(nodes: Sequence[Any], edges: Sequence[Any]) -> Dict[str, Any]:
    """聚合四组校验 + 总体结论。"""
    cov = check_coverage(nodes)
    dang = check_dangling(nodes, edges)
    acyc = check_acyclic(nodes, edges)
    core = check_core(nodes)

    overall = cov["ok"] and dang["ok"] and acyc["ok"] and core["ok"]
    return {
        "overall_ok": overall,
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "core": core["core_count"],
        },
        "checks": {
            "coverage": cov,
            "dangling": dang,
            "acyclic": acyc,
            "core": core,
        },
    }


# --------------------------------------------------------------------------- #
# DB 读取层（仅 SELECT，不改数据）
# --------------------------------------------------------------------------- #
async def _fetch(dsn: str) -> Tuple[list, list]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        nodes = await conn.fetch(
            "SELECT code, title, subject, importance, book, chapter, section "
            "FROM kb_knowledge_points ORDER BY code"
        )
        edges = await conn.fetch(
            "SELECT kp_code, prereq_code, relation_type "
            "FROM kb_kp_prerequisites"
        )
        return list(nodes), list(edges)
    finally:
        await conn.close()


async def verify_from_db(dsn: str, as_json: bool = False) -> int:
    nodes, edges = await _fetch(dsn)
    report = build_dag_report(nodes, edges)

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    return 0 if report["overall_ok"] else 1


def _print_human(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print(f"知识点 DAG 验收：{s['nodes']} 节点 / {s['edges']} 边 / {s['core']} 核心\n")

    c = report["checks"]
    print(f"[覆盖度] {'PASS' if c['coverage']['ok'] else 'FAIL'} "
          f"(缺失 {len(c['coverage']['missing_fields'])} 项, 重复 code {len(c['coverage']['duplicate_codes'])} 个)")
    if c["coverage"]["missing_fields"]:
        for m in c["coverage"]["missing_fields"][:10]:
            print(f"    - {m}")
    if c["coverage"]["duplicate_codes"]:
        print(f"    - 重复: {c['coverage']['duplicate_codes'][:10]}")

    print(f"[悬空边] {'PASS' if c['dangling']['ok'] else 'FAIL'} "
          f"({len(c['dangling']['dangling'])} 条)")
    for d in c["dangling"]["dangling"][:10]:
        print(f"    - {d['edge']}: {d['reason']}")

    a = c["acyclic"]
    print(f"[无环  ] {'PASS' if a['ok'] else 'FAIL'} "
          f"(拓扑覆盖 {a['topo_covered']}/{a['total_nodes']})")
    if not a["ok"]:
        print(f"    - 成环节点: {a['cycle_nodes'][:10]}")

    print(f"[核心标记] {'PASS' if c['core']['ok'] else 'FAIL'} (core={c['core']['core_count']})")

    print(f"\n总体: {'✅ 通过' if report['overall_ok'] else '❌ 不通过'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="知识点 DAG 只读验收")
    ap.add_argument("--dsn", default=os.environ.get("DB_DSN"), help="覆盖 DB_DSN 环境变量")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    if not args.dsn:
        print("缺少 DB_DSN（环境变量或 --dsn）", file=sys.stderr)
        raise SystemExit(2)
    code = asyncio.run(verify_from_db(args.dsn, as_json=args.json))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
