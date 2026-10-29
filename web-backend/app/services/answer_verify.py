"""答案双源交叉校验的落库与流转（M2-9 / G1）。

纯规则在 ``app/core/answer_verify.py``，这里只做四件事：

1. ``run_checks``：按当前卷面答案 + 题库现行答案 + 代入检验，重算整卷校验状态并落库；
2. ``gate``：发布前把「不能发」的题整理成结构化 violations（发布接口据此 409）；
3. ``confirm``：教师确认（可顺手修正答案），确认后放行；
4. ``_llm_suggest``：可选，租户 LLM 独立求解**只作建议**，永不影响通过与否。

为什么不每次现算
----------------
教师的「我确认过这道题的答案」本身是一条数据：谁、何时、凭什么。它必须能跨
刷新页面存活，所以落库。代价是**答案一改，旧确认必须失效**——否则教师确认了 A，
之后有人把答案改成 B，确认还挂着，等于给人盖章走空白。``run_checks`` 用
``answer`` 列做锚点：确认时的答案与当前答案不等价 → 回落待确认。

第二来源目前只有题库
--------------------
``exam_paper_questions.content_hash`` 回指 ``course_kb``。刚组完卷时两边必然相同
（快照就是抄来的），那是**同源**，不算验证；只有组卷之后题库被修订过、两边出现
差异，两个值才来自不同时点/不同写入方，比对才有意义。这正是 G1 想抓的场景：
「题库改了答案，但已下发的卷还按旧答案判分」。真实双源（原卷答案/第二题源）
在 M4 采集卷入库时接进来即可，判定逻辑无需改动。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.answer_verify import (
    SOURCE_KB,
    SOURCE_MECHANICAL,
    SOURCE_PAPER,
    SOURCE_TEACHER,
    STATUS_CONFLICT,
    STATUS_MECHANICAL,
    STATUS_TEACHER,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    evaluate,
    gate_message,
    is_passing,
    summarize,
)
from app.core.config import settings
from app.core.equiv import expand_answer_values, norm_text
from app.models.course_kb import CourseQuestion
from app.models.ops import ExamAnswerCheck, ExamPaper, ExamPaperQuestion

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get(q: Any, name: str, default: Any = None) -> Any:
    return q.get(name, default) if isinstance(q, dict) else getattr(q, name, default)


async def kb_answers(
    db: AsyncSession, tenant_id: Optional[str], questions: Sequence[Any]
) -> Dict[str, str]:
    """按 content_hash 取题库现行答案（严格按租户过滤，跨租户当不存在）。"""
    hashes = [h for h in (_get(q, "content_hash") for q in questions) if h]
    if not hashes or not tenant_id:
        return {}
    rows = (
        await db.execute(
            select(CourseQuestion.content_hash, CourseQuestion.answer).where(
                CourseQuestion.tenant_id == tenant_id,
                CourseQuestion.content_hash.in_(hashes),
            )
        )
    ).all()
    return {h: (a or "") for h, a in rows if a}


def secondary_of(
    question: Any, kb_map: Mapping[str, str]
) -> Optional[Dict[str, str]]:
    """该题是否存在**独立**第二来源。

    只有「题库现行答案与卷面答案不同源」才算——完全相同是同源，拿来当验证等于
    自己给自己盖章。
    """
    h = _get(question, "content_hash")
    answer = _get(question, "answer") or ""
    if not h or not answer.strip():
        return None
    kb = kb_map.get(h)
    if not kb or not kb.strip():
        return None
    if norm_text(kb) == norm_text(answer):
        return None
    return {"source": SOURCE_KB, "answer": kb.strip()}


async def run_checks(
    db: AsyncSession,
    paper: ExamPaper,
    questions: Sequence[Any],
    llm: bool = False,
) -> List[ExamAnswerCheck]:
    """重算整卷校验状态并落库（幂等）。返回按 seq 排序的行。

    ``llm=True`` 且配置开启时才调租户模型给建议；建议写进 ``llm_*`` 列，
    **不参与判定**，也不改答案。
    """
    tenant_id = paper.tenant_id
    existing = {
        r.paper_question_id: r
        for r in (
            await db.execute(
                select(ExamAnswerCheck).where(ExamAnswerCheck.paper_id == paper.id)
            )
        ).scalars().all()
    }
    kb_map = await kb_answers(db, tenant_id, questions)
    now = _utcnow()
    rows: List[ExamAnswerCheck] = []

    for q in questions:
        answer = (_get(q, "answer") or "").strip()
        sec = secondary_of(q, kb_map)
        res = evaluate(
            _get(q, "qtype"),
            answer,
            multi=bool(_get(q, "multi", False)),
            options=_get(q, "options"),
            stem=_get(q, "stem") or "",
            secondary=sec,
        )
        row = existing.get(_get(q, "id"))
        if row is None:
            row = ExamAnswerCheck(
                tenant_id=tenant_id,
                paper_id=paper.id,
                paper_question_id=_get(q, "id"),
                seq=_get(q, "seq") or 0,
                qtype=_get(q, "qtype"),
            )
            db.add(row)

        # 教师确认的有效性锚点：确认过的答案没变就保留确认，变了就作废。
        keep_confirm = (
            row.status == STATUS_TEACHER
            and norm_text(row.answer or "") == norm_text(answer)
            and answer != ""
        )
        row.seq = _get(q, "seq") or 0
        row.qtype = _get(q, "qtype")
        row.answer = answer
        row.secondary_source = res["secondary_source"]
        row.secondary_answer = res["secondary_answer"]
        row.mechanical_code = res["mechanical_code"]
        row.mechanical_detail = res["mechanical_detail"]
        row.suspect = bool(res["suspect"])
        row.detail = res["detail"]
        if not keep_confirm:
            row.status = res["status"]
            if res["status"] != STATUS_TEACHER:
                row.confirmed_by = None
                row.confirmed_at = None
                row.confirmed_comment = None
        if llm:
            hint = await _llm_suggest(db, tenant_id, q)
            if hint:
                row.llm_model = hint.get("model")
                row.llm_answer = hint.get("answer")
                row.llm_agree = hint.get("agree")
        row.checked_at = now
        row.updated_at = now
        rows.append(row)

    await db.commit()
    for r in rows:
        await db.refresh(r)
    rows.sort(key=lambda r: (r.seq or 0))
    return rows


async def load_checks(
    db: AsyncSession, paper: ExamPaper, questions: Sequence[Any]
) -> List[ExamAnswerCheck]:
    """读已有校验记录；一道都没有时先跑一遍（首次进页面就有结论，不用先点按钮）。"""
    rows = (
        await db.execute(
            select(ExamAnswerCheck)
            .where(ExamAnswerCheck.paper_id == paper.id)
            .order_by(ExamAnswerCheck.seq)
        )
    ).scalars().all()
    if not rows and questions:
        return await run_checks(db, paper, questions)
    return list(rows)


def violations(rows: Sequence[ExamAnswerCheck]) -> List[Dict[str, Any]]:
    """把不能发布的题整理成发布接口用的 violations（结构与 M1-3 一致）。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if is_passing(r.status):
            continue
        code = "ANSWER_CONFLICT" if r.status == STATUS_CONFLICT else "ANSWER_UNVERIFIED"
        out.append(
            {
                "seq": r.seq or 0,
                "qtype": r.qtype or "",
                "code": code,
                "message": f"第 {r.seq} 题："
                + gate_message(r.status, bool(r.suspect), r.detail or ""),
                "status": r.status,
                "answer": r.answer,
                "secondary_source": r.secondary_source,
                "secondary_answer": r.secondary_answer,
                "suspect": bool(r.suspect),
            }
        )
    return out


async def gate(
    db: AsyncSession, paper: ExamPaper, questions: Sequence[Any]
) -> Tuple[List[ExamAnswerCheck], List[Dict[str, Any]]]:
    """发布闸门：先刷新校验，再返回（全部记录, 拦截项）。"""
    rows = await run_checks(db, paper, questions)
    return rows, violations(rows)


async def confirm(
    db: AsyncSession,
    paper: ExamPaper,
    questions: Sequence[Any],
    seqs: Optional[Iterable[int]] = None,
    actor: Optional[str] = None,
    comment: Optional[str] = None,
    answers: Optional[Mapping[int, str]] = None,
) -> List[ExamAnswerCheck]:
    """教师确认（可顺手修正答案）。``seqs`` 为空 = 本卷全部「需教师出手」的题。

    修正答案只在**未发布**时允许：已下发的卷子答案一改，历史成绩的判据就变了
    （学生在结果页看到的「正确答案」也会跟着变），那是改分事故而不是修答案。
    """
    by_seq = {int(_get(q, "seq") or 0): q for q in questions}
    targets = [int(s) for s in (seqs or []) if int(s) in by_seq]
    # 显式给了修正答案的题也算目标：即便它当前是「代入检验通过」，
    # 教师既然改答案就说明他不同意机器结论，必须重新判定并留确认。
    for key in (answers or {}):
        try:
            seq = int(key)
        except (TypeError, ValueError):
            continue
        if seq in by_seq and seq not in targets:
            targets.append(seq)
    if not targets:
        rows0 = await load_checks(db, paper, questions)
        targets = [
            int(r.seq) for r in rows0 if not is_passing(r.status) and int(r.seq) in by_seq
        ]
    if not targets:
        return await load_checks(db, paper, questions)

    fixed = 0
    for seq in targets:
        q = by_seq[seq]
        new = (answers or {}).get(seq)
        if new is None:
            new = (answers or {}).get(str(seq))  # JSON 键可能是字符串
        new = (new or "").strip()
        if not new or norm_text(new) == norm_text(_get(q, "answer") or ""):
            continue
        if paper.published:
            raise ValueError("试卷已发布，不能直接改答案（会让历史成绩的判据变化）")
        q.answer = new
        q.answer_values = expand_answer_values(new) or None
        fixed += 1
    if fixed:
        await db.flush()

    rows = await run_checks(db, paper, questions)
    now = _utcnow()
    wanted = set(targets)
    for r in rows:
        if int(r.seq) not in wanted:
            continue
        r.status = STATUS_TEACHER
        r.confirmed_by = actor
        r.confirmed_at = now
        r.confirmed_comment = (comment or "").strip() or None
        r.updated_at = now
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


async def summary_of(
    db: AsyncSession,
    paper: ExamPaper,
    questions: Sequence[Any],
    refresh: bool = False,
    llm: bool = False,
) -> Tuple[List[ExamAnswerCheck], Dict[str, Any]]:
    if refresh:
        rows = await run_checks(db, paper, questions, llm=llm)
    else:
        rows = await load_checks(db, paper, questions)
    return rows, summarize(rows)


# ---------------------------------------------------------------- LLM 建议（可选）
_SOLVE_PROMPT = """你是高中数学老师。请独立求解下面的题目，只输出最终答案，不要过程。

题目：{stem}
{options}
要求：
1. 只输出答案本身（选择题输出选项字母，多个用逗号分隔；填空题输出最终值）。
2. 多解必须全部列出，用「或」分隔。
3. 不确定就输出「不确定」，不要猜。"""


async def _llm_suggest(
    db: AsyncSession, tenant_id: Optional[str], question: Any
) -> Optional[Dict[str, Any]]:
    """租户 LLM 独立求解，**只作建议**。

    失败一律返回 None（不抛）：G1 的价值在拦错答案，不在「模型说了什么」。
    模型挂了一次就卡住发布流程，是拿外部依赖当发布条件。
    """
    if not settings.answer_verify_llm or not tenant_id:
        return None
    try:
        import httpx

        from app.services import byom

        cfg = await byom.get_config(db, tenant_id)
        if cfg is None or not cfg.base_url or not cfg.api_key_enc:
            return None
        api_key = byom.decrypt_api_key(cfg.api_key_enc)
        opts = _get(question, "options")
        opt_text = ""
        if isinstance(opts, dict) and opts:
            opt_text = "\n选项：" + "；".join(f"{k}. {v}" for k, v in opts.items())
        prompt = _SOLVE_PROMPT.format(
            stem=(_get(question, "stem") or "")[:2000], options=opt_text
        )
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": cfg.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 256,
                },
            )
        if resp.status_code != 200:
            return None
        data = resp.json()
        text = (
            (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        if not text:
            return None
        from app.core.answer_verify import answer_equivalent

        qtype = _get(question, "qtype")
        agree = answer_equivalent(
            qtype,
            text,
            _get(question, "answer") or "",
            multi=bool(_get(question, "multi", False)),
            options=opts if isinstance(opts, dict) else None,
        )
        return {"model": cfg.model, "answer": text[:500], "agree": agree}
    except Exception as exc:  # noqa: BLE001 —— 建议失败绝不影响发布流程
        log.warning("answer_verify.llm_suggest failed: %s", exc)
        return None
