"""M4-4 采集卷入库两条路径（都做）。

卡面要求两条路径都实现，由教师在采集任务上选（``crawl_job.path``）：

* **P1 整卷入库**：成套卷落 ``exam_paper(source='auto_crawl')``（``published=False``），
  卷内题**仍逐条进 ``course_kb``**——保证题库统一可复用，不是「成了卷就脱离题库」。
* **P2 拆题入库**：题只进 ``course_kb``（``status='draft'``），不成卷；
  之后由 M4-2 规则组卷按需重组。

共同约束：

1. 一律 ``draft`` 待审（M3-7 审核流），不自动发布；
2. 来源可追溯：题库行带 ``source_tag='auto_crawl'``，卷头带 ``source='auto_crawl'``；
3. 可一键回退：按 ``crawl_run`` 批量撤销本次入库（题库行 + 卷头 + 卷题），
   只删本次新建的行，不碰教师改过的题（``status != 'draft'`` 的保留）。

为什么回退要按 run 而不是按 job：同一条采集任务会跑很多次，
按 job 回退会把历史好数据一起删掉。
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exam_types import (
    QTYPE_CHOICE, QTYPE_SUBJECTIVE, is_known_paper_type, to_qtype,
)
from app.core.intake_guard import (  # T-CAP4 三闸（纯函数在 core，写回在 kb_guard）
    CRAWL_TAG_BASE, text_fingerprint,
)
from app.models.course_kb import CourseQuestion
from app.models.ops import CrawlJob, ExamPaper, ExamPaperQuestion, ExamPaperIntake
from app.services import compliance_queue, kb_guard

log = logging.getLogger(__name__)

PATH_P1 = "P1"
PATH_P2 = "P2"

# T-CAP4 采集入库校验闸门开关（默认开；置 0/false 可关，便于回滚）
GUARD_ENABLED = os.environ.get(
    "CRAWL_INTAKE_GUARD_ENABLED", "true"
).strip().lower() not in ("0", "false", "no", "off")


def normalize_item(
    raw: Dict[str, Any],
    *,
    tenant_id: str,
    grade: str,
    subject: str,
) -> Optional[Dict[str, Any]]:
    """采集行 -> course_kb 行（``status=draft`` / ``source_tag=auto_crawl``）。

    缺题干或答案时返回 None：无答案的题进不了组卷，入库只会制造脏数据。
    """
    stem = str(raw.get("stem") or "").strip()
    # LLM 抽 LaTeX 题常在末尾残留转义反斜杠（"(\quad)\\"），且每次个数不稳定。
    # 不剥掉会让 content_hash 每次漂移：同一道题反复入库、反复建卷。
    stem = stem.rstrip("\\").strip()
    if not stem:
        return None
    answer = str(raw.get("answer") or "").strip()
    if not answer:
        return None
    qtype = str(raw.get("type") or "解答").strip() or "解答"
    options = raw.get("options")
    if isinstance(options, dict):
        options = [f"{k}. {v}" for k, v in options.items()]
    elif not isinstance(options, list):
        options = None
    kps = raw.get("knowledge_points") or []
    if isinstance(kps, str):
        kps = [k for k in kps.replace("，", "、").split("、") if k.strip()]
    try:
        difficulty = int(raw.get("difficulty") or 3)
    except (TypeError, ValueError):
        difficulty = 3
    content_hash = str(raw.get("content_hash") or "").strip()
    if not content_hash:
        content_hash = hashlib.sha256(
            f"{stem}|{qtype}|{answer}".encode("utf-8")
        ).hexdigest()
    return {
        "subject": str(raw.get("subject") or subject),
        "grade": str(raw.get("grade") or grade),
        "chapter": raw.get("chapter"),
        "type": qtype,
        "stem": stem,
        "options": options,
        "answer": answer,
        "analysis": raw.get("analysis"),
        "difficulty": min(5, max(1, difficulty)),
        "paper_type": raw.get("paper_type"),
        "source_province": raw.get("source_province"),
        "knowledge_points": [str(k) for k in kps if str(k).strip()],
        "content_hash": content_hash,
        "tenant_id": tenant_id,
        "source_tag": "auto_crawl",
        "status": "draft",
    }


def _paper_type_of(items: Sequence[Dict[str, Any]]) -> str:
    """``exam_papers.paper_type`` 只有 VARCHAR(16)，且内部按三种枚举校验。

    LLM 抽取常把完整试卷名塞进这个字段（如「2018年数学试卷（理科）（新课标ⅰ）」），
    直接写库会 StringDataRightTruncation，把整批采集事务一起回滚。非枚举值一律落
    ``mixed``：卷型是机器校验口径，试卷名要留就放 title。
    """
    raw = str(items[0].get("paper_type") or "").strip() if items else ""
    return raw if is_known_paper_type(raw) else "mixed"


async def _insert_unique(
    db: AsyncSession, rows: Sequence[Dict[str, Any]]
) -> tuple[List[CourseQuestion], int]:
    created: List[CourseQuestion] = []
    duplicated = 0
    for row in rows:
        # content_hash 对标点/LaTeX 格式敏感，光靠它挡不住「同题不同写法」：
        # 真实验收中 LLM 两次抽取同一道题只差尾部反斜杠，20 题全部重新入库并建了新卷。
        # 叠加持久化 text_fp（T-CAP4 迁移 0027，与三闸同一指纹）做入库前去重：
        # 这里直接跳过，比入库后再隔离更干净（不会制造一堆待裁决脏题和空卷）。
        fp = text_fingerprint(row.get("stem"))
        conds = [CourseQuestion.content_hash == row["content_hash"]]
        if fp:
            conds.append(CourseQuestion.text_fp == fp)
        exists = (
            await db.execute(
                select(CourseQuestion.id).where(or_(*conds))
            )
        ).first()
        if exists:
            duplicated += 1
            continue
        obj = CourseQuestion(**row)
        db.add(obj)
        await db.flush()
        created.append(obj)
    return created, duplicated


async def apply_intake_guard(
    db: AsyncSession, created: Sequence[CourseQuestion],
) -> Dict[str, Any]:
    """T-CAP4：对刚入库的采集题跑三闸，按裁定改写 status/source_tag。

    判定与写回逻辑统一在 ``app.services.kb_guard``（三条入库链路共用），本函数只
    固定采集链路的口径：**全库**去重索引 + ``manual_review`` 也打标（机器抽取来源，
    「未自动验证」是有用信号）。

    - 明确错误 → ``status=quarantine`` + ``source_tag=auto_crawl:guard_quarantine``；
    - 无法判定 → 保持 ``draft`` + ``source_tag=auto_crawl:guard_manual_review`` 打标；
    - 通过 → 保持 ``auto_crawl`` 原样。
    保留 ``auto_crawl`` 前缀是为了兼容回退（rollback_run）与既有隔离谓词。
    """
    if not GUARD_ENABLED or not created:
        return kb_guard.empty_stats(GUARD_ENABLED, len(created), kb_guard.CHAIN_CRAWL)
    return await kb_guard.apply_entry_guard(
        db, created,
        base_tag=CRAWL_TAG_BASE,
        chain=kb_guard.CHAIN_CRAWL,
        scope_tenant_id=None,   # 采集题跨来源重复也要抓，按全库判定
        tag_manual=True,         # 机器抽取：「未自动验证」必须可见
    )


async def intake(
    db: AsyncSession,
    job: CrawlJob,
    *,
    items: Sequence[Dict[str, Any]],
    paper_title: Optional[str] = None,
    run_id: Optional[int] = None,
) -> Dict[str, Any]:
    """按 job.path 入库；返回 ``{path, inserted, duplicated, rejected, paper_id}``。"""
    path = (job.path or PATH_P2).upper()
    if path not in (PATH_P1, PATH_P2):
        raise ValueError("path 只能是 P1/P2")

    normalized: List[Dict[str, Any]] = []
    rejected = 0
    for raw in items or []:
        row = normalize_item(
            raw,
            tenant_id=job.tenant_id,
            grade=str(raw.get("grade") or "高三"),
            subject=str(raw.get("subject") or "数学"),
        )
        if row is None:
            rejected += 1
            continue
        normalized.append(row)

    created, duplicated = await _insert_unique(db, normalized)

    # T-CAP4：入库即校验——三闸裁定明确错误的自动隔离，无法判定的打标待人工看。
    # 必须在合规入队前跑，让隔离题以正确状态进入队列。
    guard = await apply_intake_guard(db, created)

    # 合规入队（0022）：采集进来的 draft 题同样要进审核队列，
    # 不能因为「不是教师手录」就绕开合规初审。事务由调用方统一提交。
    for obj in created:
        try:
            async with db.begin_nested():
                await compliance_queue.enqueue_question(db, obj, source="crawl")
        except Exception as e:  # noqa: BLE001 队列不阻塞采集入库
            log.warning("合规入队失败（采集题已入库）question_id=%s: %s", obj.id, e)

    paper: Optional[ExamPaper] = None
    # 三闸隔离的题不进卷：它们是已判定的重复/错误数据，该走异常队列由人裁决，
    # 不能悄悄混进草稿卷。整批都被隔离时干脆不建卷，避免天天多一张脏卷。
    paper_rows = [r for r in created if r.status != "quarantine"]
    if path == PATH_P1 and paper_rows:
        paper = ExamPaper(
            tenant_id=job.tenant_id,
            title=paper_title or f"{job.name}（采集卷）",
            paper_type=_paper_type_of(items),
            grade=str(paper_rows[0].grade or "高三"),
            subject=str(paper_rows[0].subject or "数学"),
            knowledge_points=sorted({
                kp for r in paper_rows for kp in (r.knowledge_points or [])
            }),
            version=1,
            source="auto_crawl",
            published=False,
        )
        db.add(paper)
        await db.flush()
        for seq, r in enumerate(paper_rows, start=1):
            qtype, multi = to_qtype(r.type) or (QTYPE_CHOICE, False)
            db.add(ExamPaperQuestion(
                paper_id=paper.id,
                seq=seq,
                question_id=r.id,
                content_hash=r.content_hash,
                score=10 if qtype == QTYPE_SUBJECTIVE else 5,
                qtype=qtype,
                multi=multi,
                stem=r.stem,
                options=r.options,
                answer=r.answer or "",
                analysis=r.analysis,
                difficulty=r.difficulty,
                knowledge_points=r.knowledge_points or [],
            ))
        db.add(ExamPaperIntake(
            paper_id=paper.id,
            tenant_id=job.tenant_id,
            job_id=job.id,
            run_id=run_id,
            source="auto_crawl",
            # 回退要按这次运行精确撤销，必须记下本批题 id
            question_ids=[r.id for r in created],
        ))
    elif run_id is not None and created:
        # P2 也留一条 intake 记录：回退时才知道这批题是哪次采集进来的
        db.add(ExamPaperIntake(
            paper_id=None,
            tenant_id=job.tenant_id,
            job_id=job.id,
            run_id=run_id,
            source="auto_crawl",
            question_ids=[r.id for r in created],
        ))
        await db.flush()

    return {
        "path": path,
        "inserted": len(created),
        "duplicated": duplicated,
        "rejected": rejected,
        "question_ids": [r.id for r in created],
        "paper_id": paper.id if paper is not None else None,
        "published": False,
        "guard": guard,  # T-CAP4 三闸裁定统计
    }


async def rollback_run(
    db: AsyncSession, *, tenant_id: str, run_id: int
) -> Dict[str, Any]:
    """按采集运行一键回退：删掉本次新建的草稿/隔离题与整卷，保留已审核/教师改过的题。

    T-CAP4：guard 打标后 source_tag 变成 ``auto_crawl:guard_*``，故用 startswith
    兼容；status 放宽到 ``draft`` + ``quarantine``（隔离题也要能回退清除），
    published 仍保留——不删教师已放行的数据。
    """
    intakes = (
        await db.execute(
            select(ExamPaperIntake).where(
                ExamPaperIntake.tenant_id == tenant_id,
                ExamPaperIntake.run_id == run_id,
            )
        )
    ).scalars().all()

    question_ids = sorted({
        int(r) for row in intakes for r in (row.question_ids or [])
    })
    removed_questions = 0
    if question_ids:
        rows = (
            await db.execute(
                select(CourseQuestion).where(
                    CourseQuestion.id.in_(question_ids),
                    CourseQuestion.tenant_id == tenant_id,
                    CourseQuestion.source_tag.startswith("auto_crawl"),
                    CourseQuestion.status.in_(("draft", "quarantine")),
                )
            )
        ).scalars().all()
        for r in rows:
            await db.delete(r)
            removed_questions += 1

    removed_papers = 0
    for row in intakes:
        if not row.paper_id:
            continue
        paper = (
            await db.execute(
                select(ExamPaper).where(
                    ExamPaper.id == row.paper_id,
                    ExamPaper.tenant_id == tenant_id,
                    ExamPaper.source == "auto_crawl",
                    ExamPaper.published.is_(False),
                )
            )
        ).scalar_one_or_none()
        if paper is None:
            continue
        qs = (
            await db.execute(
                select(ExamPaperQuestion).where(ExamPaperQuestion.paper_id == paper.id)
            )
        ).scalars().all()
        for q in qs:
            await db.delete(q)
        await db.delete(paper)
        removed_papers += 1

    for row in intakes:
        await db.delete(row)
    return {
        "removed_questions": removed_questions,
        "removed_papers": removed_papers,
        "intake_rows": len(intakes),
    }
