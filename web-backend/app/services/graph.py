"""知识图谱可视化数据服务（T-B5）。

为 App「图谱」页提供一次性可渲染的 DAG 快照，学情叠层在同一份数据里：
  - 节点：kb_knowledge_points 元数据 + 布局深度（adaptive.depth_map 复用）
  - 边：kb_kp_prerequisites（kp -> prereq 方向，同 T-B2 约定）
  - 掌握度：student_mastery 衰减后分值（mastery.load_mastery_map 复用）
  - ZPD 高亮：adaptive.compute_zpd 复用（前置就绪、自身未掌握才推）
  - 题目数：course_kb_steps.kp_code 聚合（该点下有多少题可练）

与 mastery.py / adaptive.py 同一约定：
  - 纯函数（组装/统计/配色带）不连库，可单测；
  - DB 读写集中在带 db 参数的薄层；
  - 学科过滤时，边只保留两端都在节点集内的（跨学科悬挂边丢弃，
    深度与 ZPD 都在子图上计算，与 App 所见一致）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import adaptive
from app.services import mastery

# 配色带阈值：与 T-B2 / T-B3 完全对齐（<0.6 薄弱、>=0.8 已掌握）
WEAK_THRESHOLD = mastery.WEAK_THRESHOLD            # 0.6
MASTERED_THRESHOLD = mastery.MASTERED_THRESHOLD    # 0.8


# --------------------------------------------------------------------------- #
# 纯函数层（不连库，便于测试）
# --------------------------------------------------------------------------- #

def mastery_band(m: Optional[float]) -> str:
    """掌握度 -> 配色带（App 端按此着色，阈值与后端一致）。

    none=未学过 / weak=薄弱(<0.6) / medium=巩固中(0.6~0.8) / strong=已掌握(>=0.8)
    """
    if m is None:
        return "none"
    if m < WEAK_THRESHOLD:
        return "weak"
    if m < MASTERED_THRESHOLD:
        return "medium"
    return "strong"


def summarize(nodes: Sequence[dict], edges: Sequence[Tuple[str, str]]) -> dict:
    """图谱统计头（App 顶部一行小字）：总量 / 各配色带计数 / ZPD 数 / 边数 / 最大深度。"""
    bands: Dict[str, int] = {}
    for n in nodes or []:
        b = n.get("band") or "none"
        bands[b] = bands.get(b, 0) + 1
    return {
        "total": len(nodes or []),
        "mastered": bands.get("strong", 0),
        "weak": bands.get("weak", 0),
        "medium": bands.get("medium", 0),
        "untouched": bands.get("none", 0),
        "in_zpd": sum(1 for n in (nodes or []) if n.get("zpd")),
        "edges": len(edges or []),
        "max_depth": max((n.get("depth") or 0 for n in (nodes or [])), default=0),
    }


def assemble_node(
    meta: dict,
    mastery_score: Optional[float],
    practice: Optional[dict],
    in_zpd: bool,
    depth: int,
    question_count: int,
    prereqs: Sequence[str],
) -> dict:
    """单节点装配：元数据 + 学情叠层 + 布局字段（纯函数）。"""
    p = practice or {}
    m = None if mastery_score is None else round(float(mastery_score), 4)
    return {
        "code": meta.get("code"),
        "title": meta.get("title"),
        "chapter": meta.get("chapter"),
        "section": meta.get("section"),
        "subject": meta.get("subject"),
        "importance": meta.get("importance"),
        "depth": int(depth or 0),
        "mastery": m,
        "band": mastery_band(m),
        "zpd": bool(in_zpd),
        "practice_count": int(p.get("practice_count") or 0),
        "correct_count": int(p.get("correct_count") or 0),
        "wrong_count": int(p.get("wrong_count") or 0),
        "last_practiced_at": p.get("last_practiced_at"),
        "question_count": int(question_count or 0),
        "prereqs": sorted({x for x in (prereqs or []) if x}),
    }


# --------------------------------------------------------------------------- #
# DB 薄层
# --------------------------------------------------------------------------- #

async def load_kp_meta(db: AsyncSession, subject: Optional[str] = None) -> List[dict]:
    """知识点元数据（可按学科过滤）。"""
    sql = (
        "SELECT code, title, chapter, section, subject, importance "
        "FROM kb_knowledge_points"
    )
    if subject:
        sql += " WHERE subject = :s"
        sql += " ORDER BY code"
        res = await db.execute(text(sql).bindparams(s=subject))
    else:
        sql += " ORDER BY code"
        res = await db.execute(text(sql))
    return [
        {
            "code": r[0], "title": r[1], "chapter": r[2], "section": r[3],
            "subject": r[4], "importance": r[5],
        }
        for r in res.all()
    ]


async def load_question_counts(
    db: AsyncSession, subject: Optional[str] = None
) -> Dict[str, int]:
    """每个知识点下的题目数（去重 content_hash；经 steps.kp_code 归因）。"""
    sql = (
        "SELECT s.kp_code AS kp, count(DISTINCT s.content_hash) AS n "
        "FROM course_kb_steps s JOIN course_kb c ON c.content_hash = s.content_hash "
        "WHERE s.kp_code IS NOT NULL AND btrim(s.kp_code) <> ''"
    )
    if subject:
        sql += " AND c.subject = :s"
        sql += " GROUP BY s.kp_code"
        res = await db.execute(text(sql).bindparams(s=subject))
    else:
        sql += " GROUP BY s.kp_code"
        res = await db.execute(text(sql))
    return {r[0]: int(r[1]) for r in res.all() if r[0]}


async def load_practice_stats(
    db: AsyncSession, student_id: str
) -> Dict[str, dict]:
    """学生练习统计（原始计数，供节点详情卡展示；不含衰减计算）。

    键兼容同 load_mastery_map：按名称落库的历史行翻译成 code，
    翻译不到的名称原样保留（匹配不上节点即不可见）。
    """
    res = await db.execute(
        text(
            "SELECT knowledge_point, practice_count, correct_count, wrong_count, "
            "last_practiced_at FROM student_mastery WHERE student_id = :sid"
        ).bindparams(sid=student_id)
    )
    rows = [r for r in res.all() if r[0]]
    if not rows:
        return {}
    title_map = await mastery.load_kp_title_map(db)
    out: Dict[str, dict] = {}
    for r in rows:
        kp = title_map.get(r[0], r[0])
        out[kp] = {
            "practice_count": r[1], "correct_count": r[2],
            "wrong_count": r[3], "last_practiced_at": r[4],
        }
    return out


async def paper_graph_db(
    db: AsyncSession,
    paper_id: int,
    student_id: Optional[str] = None,
) -> dict:
    """M3-4：按卷聚合知识点，返回子图 + 前驱/后继高亮。

    与 ``knowledge_graph_db`` 的区别只在**节点集合**：这里取卷内题的
    ``question_kp`` 绑定（回落到题库 ``knowledge_points`` 标签），再把
    这些点的前驱（学习前置）与后继（可延伸）补进来，并分别标记
    ``in_paper`` / ``is_prereq`` / ``is_successor`` 供前端着色。
    """
    from app.models.ops import ExamPaperQuestion, QuestionKp
    from sqlalchemy import select as _select

    qs = (
        await db.execute(
            _select(ExamPaperQuestion).where(ExamPaperQuestion.paper_id == paper_id)
        )
    ).scalars().all()
    qids = [q.question_id for q in qs if q.question_id]
    hashes = [q.content_hash for q in qs if q.content_hash]

    paper_kps: set = set()
    if qids:
        rows = (
            await db.execute(
                _select(QuestionKp.kp_code).where(QuestionKp.question_id.in_(qids))
            )
        ).all()
        paper_kps |= {r[0] for r in rows if r[0]}
    for q in qs:
        paper_kps |= {k for k in (q.knowledge_points or []) if k}

    meta_rows = await load_kp_meta(db)
    meta_by_code = {m["code"]: m for m in meta_rows if m.get("code")}
    # 卷内标签可能是名称：能翻译成 code 的并入节点集，翻不到的单独列出（不硬塞进图）
    unresolved = sorted(k for k in paper_kps if k not in meta_by_code)
    all_edges = await mastery.load_dag_edges(db)

    prereqs: dict = {}
    successors: dict = {}
    for kp, pre in all_edges:
        prereqs.setdefault(kp, set()).add(pre)
        successors.setdefault(pre, set()).add(kp)

    core = {k for k in paper_kps if k in meta_by_code}
    highlight_pre = {p for k in core for p in prereqs.get(k, set()) if p in meta_by_code}
    highlight_post = {s for k in core for s in successors.get(k, set()) if s in meta_by_code}
    nodes_set = core | highlight_pre | highlight_post

    nodes = []
    for code in sorted(nodes_set):
        m = meta_by_code[code]
        nodes.append(
            assemble_node(
                m,
                None,
                None,
                False,
                depth=0,
                question_count=0,
                prereqs=sorted(prereqs.get(code, set())),
            )
        )
        nodes[-1].update({
            "in_paper": code in core,
            "is_prereq": code in highlight_pre and code not in core,
            "is_successor": code in highlight_post and code not in core,
        })

    edges = [
        {"from": pre, "to": kp}
        for kp, pre in all_edges
        if kp in nodes_set and pre in nodes_set
    ]
    return {
        "paper_id": paper_id,
        "student_id": student_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(nodes, [(e["from"], e["to"]) for e in edges]),
        "nodes": nodes,
        "edges": edges,
        "paper_kp_count": len(core),
        "unresolved_kps": unresolved,
        "note": "" if core else "本卷题目尚未绑定 DAG 知识点（先在题↔知识点里标注）",
    }


def question_graph_plan(
    raw_kps: Sequence[str],
    meta_by_code: Dict[str, dict],
    edges: Sequence[Tuple[str, str]],
) -> dict:
    """M3-6 纯函数：题目标签 -> 本题考点 + 一层前驱/后继 + 子图边。

    与 ``paper_graph_db`` 的关键区别是**节点集合按单题锁定**：整卷图把十道题的
    知识点混在一起，学生看结果页时定位不到「这道题到底卡在哪个点」。

    题目标签可能是 DAG code，也可能是中文名（旧数据）：能翻译的并入节点集，
    翻不到的进 ``unresolved`` 原样回报，绝不硬塞进图（避免造第二套键）。
    只展开**一层**前驱/后继：手机端逐题卡片放不下多级子图，且多级会让
    「本题考点」被淹没在一堆远亲里。
    """
    title_to_code = {
        m["title"]: code
        for code, m in meta_by_code.items()
        if m.get("title")
    }
    core: set = set()
    unresolved: List[str] = []
    for k in raw_kps or []:
        if not k:
            continue
        code = k if k in meta_by_code else title_to_code.get(k)
        if code:
            core.add(code)
        else:
            unresolved.append(k)

    prereq_map: Dict[str, set] = {}
    successors: Dict[str, set] = {}
    for kp, pre in edges or []:
        prereq_map.setdefault(kp, set()).add(pre)
        successors.setdefault(pre, set()).add(kp)

    prereqs = {
        p for k in core for p in prereq_map.get(k, set()) if p in meta_by_code
    } - core
    # 三层角色必须互斥：链 A->B->C 里若 A、C 都是本题考点，B 既是 A 的后继
    # 又是 C 的前驱，不减去 prereqs 的话 App 会把同一个节点画两遍。
    succs = {
        s for k in core for s in successors.get(k, set()) if s in meta_by_code
    } - core - prereqs
    nodes_set = core | prereqs | succs
    # from=前置、to=后置（学习流向），只保留两端都在子图里的边
    sub_edges = [
        (pre, kp) for kp, pre in (edges or [])
        if kp in nodes_set and pre in nodes_set
    ]
    return {
        "core": sorted(core),
        "prereqs": sorted(prereqs),
        "successors": sorted(succs),
        "nodes_set": nodes_set,
        "edges": sub_edges,
        "unresolved": sorted(set(unresolved)),
        "prereq_map": prereq_map,
    }


async def question_graph_db(
    db: AsyncSession,
    *,
    assignment_id: int,
    paper_id: int,
    seq: int,
    student_id: str,
    question,
) -> dict:
    """M3-6：单题知识子图（结果页逐题图谱）的 DB 装配层。

    数据来源两处并集：卷面题自带的 ``knowledge_points`` 快照（线上卷里已是
    DAG code）+ ``question_kp`` 教师标注（按 question_id，无 id 时按 content_hash）。
    掌握度走 ``mastery.load_mastery_map`` 复用衰减口径，节点深度固定为
    前置=0 / 本题=1 / 后继=2，供 App 分三层渲染。
    """
    raw = {k for k in (getattr(question, "knowledge_points", None) or []) if k}
    qid = getattr(question, "question_id", None)
    content_hash = getattr(question, "content_hash", None)
    if qid or content_hash:
        from app.models.ops import QuestionKp
        from sqlalchemy import select as _select

        stmt = _select(QuestionKp.kp_code)
        stmt = stmt.where(
            QuestionKp.question_id == qid
            if qid else QuestionKp.content_hash == content_hash
        )
        rows = (await db.execute(stmt)).all()
        raw |= {r[0] for r in rows if r[0]}

    meta_rows = await load_kp_meta(db)
    meta_by_code = {m["code"]: m for m in meta_rows if m.get("code")}
    all_edges = await mastery.load_dag_edges(db)
    plan = question_graph_plan(sorted(raw), meta_by_code, all_edges)

    nodes_set = plan["nodes_set"]
    mmap = (
        await mastery.load_mastery_map(db, student_id, sorted(nodes_set))
        if nodes_set else {}
    )

    core = set(plan["core"])
    prereqs = set(plan["prereqs"])
    nodes = []
    for code in sorted(nodes_set):
        role = "core" if code in core else ("prereq" if code in prereqs else "successor")
        depth = {"prereq": 0, "core": 1, "successor": 2}[role]
        n = assemble_node(
            meta_by_code[code],
            mmap.get(code),
            None,
            False,
            depth,
            0,
            sorted(p for p in plan["prereq_map"].get(code, set()) if p in nodes_set),
        )
        # App 按 code 分组展示即可；role 冗余一份便于调试/未来直接渲染
        n["role"] = role
        nodes.append(n)

    edges = [{"from": f, "to": t} for f, t in plan["edges"]]
    return {
        "assignment_id": assignment_id,
        "paper_id": paper_id,
        "seq": seq,
        "question_id": qid,
        "student_id": student_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(nodes, [(e["from"], e["to"]) for e in edges]),
        "core_kps": plan["core"],
        "prereq_kps": plan["prereqs"],
        "successor_kps": plan["successors"],
        "nodes": nodes,
        "edges": edges,
        "unresolved_kps": plan["unresolved"],
        "note": "" if core else "本题尚未绑定 DAG 知识点（先在题↔知识点里标注）",
    }


async def knowledge_graph_db(
    db: AsyncSession,
    student_id: str,
    subject: Optional[str] = None,
) -> dict:
    """图谱快照（DB 版）：nodes + edges + summary，一次拉取 App 即可离线渲染。

    边方向约定与 T-B2 一致：payload 中 {"from": 前置, "to": 后置}，
    即学习流向（先学 from，再学 to）。
    """
    meta_rows = await load_kp_meta(db, subject)
    codes = [r["code"] for r in meta_rows if r.get("code")]
    if not codes:
        return {
            "student_id": student_id, "subject": subject,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": summarize([], []),
            "nodes": [], "edges": [],
            "note": "无知识点数据（该学科尚未入库）",
        }

    # 学科过滤时丢弃跨学科悬挂边，保证深度/ZPD 与 App 所见子图一致
    code_set = set(codes)
    all_edges = await mastery.load_dag_edges(db)
    edges: List[Tuple[str, str]] = [
        (kp, pre) for kp, pre in all_edges if kp in code_set and pre in code_set
    ]

    mmap = await mastery.load_mastery_map(db, student_id, codes)
    zpd = adaptive.compute_zpd(mmap, edges, codes)
    depth = adaptive.depth_map(edges, codes)
    qcounts = await load_question_counts(db, subject)
    practice = await load_practice_stats(db, student_id)

    prereq_map: Dict[str, List[str]] = {}
    for kp, pre in edges:
        prereq_map.setdefault(kp, []).append(pre)

    nodes = [
        assemble_node(
            meta=r,
            mastery_score=mmap.get(r["code"]),
            practice=practice.get(r["code"]),
            in_zpd=r["code"] in zpd,
            depth=depth.get(r["code"], 0),
            question_count=qcounts.get(r["code"], 0),
            prereqs=prereq_map.get(r["code"], []),
        )
        for r in meta_rows
    ]
    # 浅的先学：按深度再按 code 排序，App 布局可直接用
    nodes.sort(key=lambda n: (n["depth"], n["code"] or ""))

    return {
        "student_id": student_id,
        "subject": subject,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize(nodes, edges),
        "nodes": nodes,
        "edges": [{"from": pre, "to": kp} for kp, pre in edges],
    }
