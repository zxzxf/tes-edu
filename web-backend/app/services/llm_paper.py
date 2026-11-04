"""M4-3 LLM 辅助组卷：按主题生成**草稿题**，绝不自动发布。

三条硬约束（卡面）：

1. **只入草稿**：``course_kb.status='draft'`` + ``source_tag='llm_generated'``；
   教师须走 M3-7 审核（``POST /exam/questions/{id}/review``）才转 ``published``，
   之后才可能被 M4-2 规则组卷抽到。
2. **不自动成卷发布**：可选地建一张 ``published=False`` 的草稿卷方便教师看，
   但即使建了，发布闸门也会因为题目未审核而 409 —— 这是有意的双保险。
3. **不可用时明确失败**：租户没配模型 / 端点 403 / 429 / 超时 / 输出不合规，
   一律返回可读原因（API 层 503/422），**绝不用模板题冒充 LLM 生成**——
   「看起来生成了，其实全是套话」比报错更糟。

输出校验与 ``app.services.parser`` 同一口径（题干非空、选择题选项 A-D 合规），
只不过这里**丢掉**不合规的题并回报原因，而不是整批失败：LLM 一次出 10 题，
坏 1 题不该让教师重来。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exam_types import (
    QTYPE_BLANK,
    QTYPE_CHOICE,
    QTYPE_SUBJECTIVE,
    check_choice_answer,
    course_types_for,
)
from app.models.course_kb import CourseQuestion
from app.models.ops import ExamPaper, ExamPaperQuestion
from app.services import byom

log = logging.getLogger(__name__)

#: 组卷提示词版本：输出结构变了就 bump，便于按版本回看历史草稿质量。
LLM_PAPER_PROMPT_VERSION = 1

LLM_PAPER_SYSTEM = (
    "你是一名高中数学教研员，按给定主题出**题目草稿**。\n"
    "【硬性】每道题必须给出可判分的答案：选择题答案必须是 A/B/C/D 中的一个"
    "（多选写连写字母），填空题给最终值，解答题给最终结论。\n"
    "【硬性】不得编造与题目不符的答案；拿不准就不要出这道题。\n"
    "只输出严格 JSON，不要包裹 markdown：\n"
    '{"questions":[{"type":"单选|多选|填空|解答","stem":"题干",'
    '"options":["A. ...","B. ...","C. ...","D. ..."],"answer":"A",'
    '"analysis":"解析","difficulty":3,"knowledge_points":["知识点"]}]}'
)

_QTYPE_TO_COURSE = {
    QTYPE_CHOICE: "选择",
    QTYPE_BLANK: "填空",
    QTYPE_SUBJECTIVE: "解答",
}


def build_prompt(
    *,
    topic: str,
    grade: str,
    subject: str,
    paper_type: str,
    count: int,
    difficulty_mix: Optional[Dict[int, int]] = None,
) -> str:
    allowed = course_types_for(paper_type)
    mix = "、".join(f"难度{k} {v}题" for k, v in sorted((difficulty_mix or {}).items()))
    return (
        f"【学段】{grade}\n【学科】{subject}\n【主题】{topic}\n"
        f"【卷型】{paper_type}（只允许题型：{'、'.join(allowed) or '不限'}）\n"
        f"【题量】{count}\n" + (f"【难度配比】{mix}\n" if mix else "")
        + "请只输出 JSON。"
    )


def _clean_options(options: Any) -> List[str]:
    if isinstance(options, dict):
        return [f"{k}. {v}" for k, v in options.items()]
    if isinstance(options, list):
        return [str(o).strip() for o in options if str(o or "").strip()]
    return []


def normalize_generated(
    payload: Any,
    *,
    subject: str,
    grade: str,
    paper_type: str,
    limit: int,
) -> Dict[str, Any]:
    """把模型输出收敛成可入库的题库行；不合规的题逐条回报原因。

    返回 ``{"items": [...], "rejected": [{"index", "reason"}]}``。
    """
    raw_items = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return {"items": [], "rejected": [{"index": 0, "reason": "输出不是题目数组"}]}

    allowed_types = set(course_types_for(paper_type))
    out: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen_stems: set = set()

    for i, raw in enumerate(raw_items):
        if len(out) >= limit:
            break
        if not isinstance(raw, dict):
            rejected.append({"index": i, "reason": "题目不是对象"})
            continue
        stem = str(raw.get("stem") or "").strip()
        if not stem:
            rejected.append({"index": i, "reason": "题干为空"})
            continue
        if stem in seen_stems:
            rejected.append({"index": i, "reason": "同批重复题干"})
            continue
        answer = str(raw.get("answer") or "").strip()
        if not answer:
            rejected.append({"index": i, "reason": "缺少答案（草稿也必须有答案）"})
            continue

        raw_type = str(raw.get("type") or "").strip()
        course_type = raw_type if raw_type in allowed_types else None
        if course_type is None:
            # 中文题型 -> 卷型白名单：LLM 常写「单选题」，这里做一次兜底映射
            mapped = {
                "单选题": "选择", "选择题": "选择", "单选": "选择",
                "多选题": "多选", "多选": "多选",
                "填空题": "填空", "填空": "填空",
                "解答题": "解答", "解答": "解答", "计算题": "解答",
            }.get(raw_type)
            if mapped and mapped in allowed_types:
                course_type = mapped
            else:
                rejected.append({
                    "index": i,
                    "reason": f"题型「{raw_type or '未给'}」不在卷型 {paper_type} 白名单内",
                })
                continue

        options = _clean_options(raw.get("options"))
        if course_type in ("选择", "单选"):
            if check_choice_answer(answer, False) is not None:
                rejected.append({"index": i, "reason": "选择题答案不是单选字母 A-D"})
                continue
            if len(options) < 2:
                rejected.append({"index": i, "reason": "选择题缺少选项"})
                continue
        elif course_type == "多选":
            if check_choice_answer(answer, True) is not None:
                rejected.append({"index": i, "reason": "多选题答案不合规"})
                continue
            if len(options) < 2:
                rejected.append({"index": i, "reason": "选择题缺少选项"})
                continue

        try:
            difficulty = int(raw.get("difficulty") or 3)
        except (TypeError, ValueError):
            difficulty = 3
        kps = raw.get("knowledge_points") or []
        if isinstance(kps, str):
            kps = [k.strip() for k in kps.split("、") if k.strip()]
        seen_stems.add(stem)
        out.append({
            "subject": subject,
            "grade": grade,
            "type": course_type,
            "stem": stem,
            "options": options or None,
            "answer": answer,
            "analysis": str(raw.get("analysis") or "").strip() or None,
            "difficulty": min(5, max(1, difficulty)),
            "knowledge_points": [str(k) for k in kps if str(k).strip()],
        })
    return {"items": out, "rejected": rejected}


async def _default_transport(
    db: AsyncSession, tenant_id: str, system: str, user: str
) -> Optional[str]:
    cfg = await byom.get_config(db, tenant_id)
    if cfg is None or not cfg.enabled or not cfg.base_url or not cfg.api_key_enc:
        return None
    import httpx

    api_key = byom.decrypt_api_key(cfg.api_key_enc)
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            cfg.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": cfg.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "max_tokens": 3000,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"模型端点返回 {resp.status_code}")
    return (
        (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    ).strip()


def parse_llm_json(text: str) -> Any:
    """剥掉可能出现的 markdown 围栏后解析 JSON；解不出来抛 ValueError。"""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
    if not body:
        raise ValueError("模型返回为空")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON：{exc}") from exc


def _content_hash(row: Dict[str, Any]) -> str:
    import hashlib

    fp = (
        f"{row.get('stem')}|{row.get('type')}|{row.get('answer') or ''}"
    ).encode("utf-8")
    return hashlib.sha256(fp).hexdigest()


async def generate_drafts(
    db: AsyncSession,
    *,
    tenant_id: str,
    topic: str,
    grade: str = "高三",
    subject: str = "数学",
    paper_type: str = "pure_choice",
    count: int = 10,
    difficulty_mix: Optional[Dict[int, int]] = None,
    transport=None,
    create_paper: bool = True,
) -> Dict[str, Any]:
    """生成草稿题并入库（``status=draft``）；返回可读报告。

    ``transport(system, user) -> str`` 可注入，便于单测不联网。默认走租户 BYOM。
    """
    prompt = build_prompt(
        topic=topic,
        grade=grade,
        subject=subject,
        paper_type=paper_type,
        count=count,
        difficulty_mix=difficulty_mix,
    )
    if transport is None:
        text = await _default_transport(db, tenant_id, LLM_PAPER_SYSTEM, prompt)
        if text is None:
            raise RuntimeError(
                "本租户未配置可用的大模型（或配置未启用），无法生成草稿题"
            )
    else:
        text = await transport(LLM_PAPER_SYSTEM, prompt)

    normalized = normalize_generated(
        parse_llm_json(text),
        subject=subject,
        grade=grade,
        paper_type=paper_type,
        limit=max(1, min(int(count or 1), 30)),
    )

    created_rows: List[CourseQuestion] = []
    duplicated = 0
    for item in normalized["items"]:
        h = _content_hash(item)
        exists = (
            await db.execute(
                select(CourseQuestion.id).where(CourseQuestion.content_hash == h)
            )
        ).first()
        if exists:
            duplicated += 1
            continue
        row = CourseQuestion(
            **item,
            content_hash=h,
            tenant_id=tenant_id,
            source_tag="llm_generated",
            status="draft",
        )
        db.add(row)
        await db.flush()
        created_rows.append(row)

    paper = None
    if create_paper and created_rows:
        paper = ExamPaper(
            tenant_id=tenant_id,
            title=f"{grade}·{subject}·{topic}（AI 草稿 {len(created_rows)}题）",
            paper_type=paper_type,
            grade=grade,
            subject=subject,
            knowledge_points=sorted({
                kp for r in created_rows for kp in (r.knowledge_points or [])
            }),
            version=1,
            source="llm_generated",
            published=False,
        )
        db.add(paper)
        await db.flush()
        for seq, r in enumerate(created_rows, start=1):
            from app.core.exam_types import to_qtype

            qtype, multi = to_qtype(r.type) or (QTYPE_CHOICE, False)
            db.add(ExamPaperQuestion(
                paper_id=paper.id,
                seq=seq,
                question_id=r.id,
                content_hash=r.content_hash,
                score=5 if qtype != QTYPE_SUBJECTIVE else 10,
                qtype=qtype,
                multi=multi,
                stem=r.stem,
                options=r.options,
                answer=r.answer or "",
                analysis=r.analysis,
                difficulty=r.difficulty,
                knowledge_points=r.knowledge_points or [],
            ))

    return {
        "created": len(created_rows),
        "duplicated": duplicated,
        "rejected": normalized["rejected"],
        "questions": [
            {
                "id": r.id,
                "type": r.type,
                "stem": r.stem,
                "answer": r.answer,
                "status": r.status,
                "source_tag": r.source_tag,
            }
            for r in created_rows
        ],
        "paper_id": paper.id if paper is not None else None,
        "published": False,
        "prompt_version": LLM_PAPER_PROMPT_VERSION,
    }
