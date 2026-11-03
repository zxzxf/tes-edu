"""教师录题（M0-1）：写入统一题库 ``course_kb`` 并双写向量 ``course_kb_embeddings``。

链路：
    POST /api/admin/questions  ──┐
    POST /api/admin/questions/batch ─┴─→ normalize（parser，与批量导入同一指纹口径）
                                        → 幂等去重（content_hash）
                                        → 落 course_kb（**status 恒为 draft**）
                                        → 向量化 → 双写 course_kb_embeddings
                                        → 向量失败仅降级，题目仍在库里

三条不可让的约束：

1. **审核隔离**：录题一律 ``draft``，发布必须走 ``POST /exam/questions/{id}/review``。
   ``content_hash`` 与 ``ingest.py`` 完全同一算法（``parser.normalize_question``），
   所以「录题」和「Excel 导入」同一道题只会有一行——换渠道不产生重复题。
2. **双写失败不阻塞入库**：embedding 任何失败都只让题目留在 ``draft``，
   由 M0-4 回填脚本补向量；接口永不因模型端点 401/429 变成 500。
3. **租户隔离**：``tenant_id`` 只取自登录态，不读请求体，避免教师代填串租户。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.equiv import expand_answer_values
from app.models.course_kb import (
    CourseKbEmbedding,
    CourseQuestion,
    QuestionCreate,
)
from app.services import embed as embed_svc
from app.services.parser import normalize_question

log = logging.getLogger(__name__)

#: 选择/单选/多选 三个值都走 choice_type 细分；其它题型忽略 choice_type
_CHOICE_TYPES = {"选择", "单选", "多选"}

_CHOICE_TYPE_NORM = {
    "single": "单选", "单选": "单选", "single_choice": "单选", "radio": "单选",
    "multi": "多选", "多选": "多选", "multi_choice": "多选", "multiple": "多选", "checkbox": "多选",
}


def _refine_type(qtype: str, choice_type: Optional[str]) -> tuple[str, Optional[str]]:
    """把「选择」按 ``choice_type`` 细分成单选/多选。

    返回 ``(type, choice_type_norm)``。非选择题保持原样、``choice_type`` 清空
    （避免出现「填空题带 single 标记」这种自相矛盾的落库数据）。
    """
    if qtype not in _CHOICE_TYPES:
        return qtype, None
    if not choice_type:
        # 来源未区分单/多选：保留「选择」，组卷装配比时按「选择」整体处理
        return qtype, None
    norm = _CHOICE_TYPE_NORM.get(str(choice_type).strip().lower(), None)
    if norm is None:
        return qtype, None
    return norm, str(choice_type).strip().lower()


def _answer_values_for(qtype: str, answer: Optional[str]) -> Optional[List[str]]:
    """由标答预展开可接受答案值（M2-4 口径）。

    选择题的字母答案统一大写：``expand_answer_values`` 会做书写层归一（小写化），
    存成 ``['b']`` 在教师端与组卷快照里读起来是「另一个答案」，容易误判人工核对。
    等价判定本身大小写不敏感，大写化只是让落库值与人眼一致。
    """
    if not answer or not str(answer).strip():
        return None
    values = expand_answer_values(str(answer))
    if qtype in _CHOICE_TYPES:
        values = [v.upper() for v in values]
    seen: List[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen or None


def _to_row(payload: QuestionCreate, tenant_id: str) -> dict:
    """请求 → course_kb 行（含 content_hash）。

    走 ``parser.normalize_question`` 而不是手写 content_hash：这是与批量导入
    **共用同一个去重指纹**的唯一保证（历史上两处各算一次曾导致重复题入库）。
    """
    raw = {
        "subject": payload.subject,
        "grade": payload.grade,
        "chapter": payload.chapter,
        "type": payload.qtype or payload.type,
        "stem": payload.stem,
        "options": payload.options,
        "answer": payload.answer,
        "analysis": payload.analysis,
        "difficulty": payload.difficulty,
        "paper_type": payload.paper_type,
        "source_province": payload.source_province,
        "knowledge_points": payload.knowledge_points,
    }
    norm = normalize_question(raw)
    qtype, choice_type = _refine_type(norm["type"], payload.choice_type)
    norm["type"] = qtype
    norm["choice_type"] = choice_type
    if qtype in _CHOICE_TYPES and norm.get("answer"):
        # 选择题标答统一大写（A/B/ACD），与选项标签、组卷快照的写法对齐
        norm["answer"] = str(norm["answer"]).strip().upper()
    norm["answer_values"] = (
        payload.answer_values or _answer_values_for(qtype, norm.get("answer"))
    )
    norm.update(tenant_id=tenant_id, source_tag="teacher", status="draft")
    return norm


async def create_questions(
    db,
    payloads: List[QuestionCreate],
    user: dict,
    *,
    transport=None,
    embed_config: Optional[dict] = None,
) -> dict:
    """录题主入口（单题/批量共用）。

    :param transport: 测试注入用的 embedding 传输层；生产传 ``None`` 走 httpx。
    :param embed_config: 测试注入用的 embedding 配置；``None`` 时按租户/全局解析。
    """
    tenant_id = (user or {}).get("tenant_id")
    if not tenant_id:
        # 没租户就没法隔离题库：宁可直接拒，也不要写出一批"无主题"
        raise HTTPException(status_code=400, detail="当前账号未挂学校（tenant_id 为空），无法录题")

    items: List[dict] = []
    created_rows: List[CourseQuestion] = []
    duplicated = 0

    for payload in payloads:
        try:
            row = _to_row(payload, tenant_id)
        except ValueError as e:  # parser 对空题干抛 ValueError
            raise HTTPException(status_code=422, detail=f"题目解析失败：{e}") from e

        existing = (
            await db.execute(
                select(CourseQuestion).where(
                    CourseQuestion.content_hash == row["content_hash"]
                )
            )
        ).scalars().first()
        if existing is not None:
            duplicated += 1
            items.append(
                {
                    "id": existing.id,
                    "content_hash": existing.content_hash,
                    "status": existing.status,
                    "duplicated": True,
                }
            )
            continue

        try:
            # SAVEPOINT：并发下另一请求刚插了同一 content_hash 时，只回滚这一条
            async with db.begin_nested():
                obj = CourseQuestion(**row)
                db.add(obj)
                await db.flush()
        except IntegrityError:
            existing = (
                await db.execute(
                    select(CourseQuestion).where(
                        CourseQuestion.content_hash == row["content_hash"]
                    )
                )
            ).scalars().first()
            duplicated += 1
            items.append(
                {
                    "id": existing.id if existing else None,
                    "content_hash": row["content_hash"],
                    "status": existing.status if existing else "draft",
                    "duplicated": True,
                }
            )
            continue

        created_rows.append(obj)
        items.append(
            {
                "id": obj.id,
                "content_hash": obj.content_hash,
                "status": obj.status,
                "duplicated": False,
            }
        )

    await db.commit()

    embedding = await _dual_write_embeddings(
        db,
        created_rows,
        tenant_id=tenant_id,
        transport=transport,
        config=embed_config,
    )

    return {
        "received": len(payloads),
        "created": len(created_rows),
        "duplicated": duplicated,
        "items": items,
        "embedding": embedding,
    }


async def _dual_write_embeddings(
    db,
    rows: List[CourseQuestion],
    *,
    tenant_id: str,
    transport=None,
    config: Optional[dict] = None,
) -> dict:
    """给新入库的题目写向量；失败只标记降级，题目仍是 draft。"""
    base = {"ok": False, "model": "", "source": "none", "reason": "", "written": 0}
    if not rows:
        # 全部命中幂等去重（重复录同一批题）：没有新行要向量化，
        # 这不是「没配端点」，reason 要写清楚，否则前端会误报配置缺失。
        return {**base, "ok": True, "reason": "无新增题目，跳过向量化"}

    contents = [embed_svc.build_content(r.stem, r.analysis) for r in rows]
    outcome = await embed_svc.embed_texts(
        contents, db=db, tenant_id=tenant_id, transport=transport, config=config
    )
    meta = {
        "ok": outcome["ok"],
        "model": outcome.get("model", ""),
        "source": outcome.get("source", "none"),
        "reason": outcome.get("reason", ""),
    }

    if not outcome["ok"]:
        # 红线：向量失败不阻塞入库。题目已在 course_kb 且 status=draft，
        # 待 M0-4 回填脚本补齐向量后再转 published。
        log.warning("录题向量化降级（题目已入库保持 draft）：%s", meta["reason"])
        return {**base, **meta, "written": 0}

    vectors = outcome["vectors"] or []
    written = 0
    for row, content, vec in zip(rows, contents, vectors):
        exists = (
            await db.execute(
                select(CourseKbEmbedding.id).where(
                    CourseKbEmbedding.content_hash == row.content_hash
                )
            )
        ).scalars().first()
        if exists is not None:  # 幂等：重录不会产生第二行向量
            continue
        db.add(
            CourseKbEmbedding(
                subject=row.subject,
                knowledge_point=(row.knowledge_points or [""])[0] if row.knowledge_points else "",
                # 本表 difficulty 是 TEXT，course_kb 是 INTEGER，必须转 str 再写
                difficulty=str(row.difficulty if row.difficulty is not None else ""),
                question=row.stem,
                answer=row.answer or "",
                content=content,
                embedding=vec,
                content_hash=row.content_hash,
                source_tag=row.source_tag,
                # M0-4：RAG 检索表必须带租户，否则别校题会被当成本校标答召回
                tenant_id=row.tenant_id,
                type=row.type or "",
            )
        )
        written += 1

    try:
        await db.commit()
    except Exception as e:  # noqa: BLE001 向量写失败同样不阻塞：题目已落库
        await db.rollback()
        log.warning("向量写入失败（题目已入库保持 draft）：%s", e)
        return {**base, **meta, "ok": False, "reason": f"向量写入失败：{str(e)[:160]}", "written": 0}

    return {**base, **meta, "written": written}
