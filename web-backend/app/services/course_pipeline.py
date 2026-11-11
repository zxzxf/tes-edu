"""M5-2 课程解析 pipeline + M5-3/M5-4 知识点入库与审核。

解析只做**确定性**切分：按标题层级（第X章/第X节/一、/1.）建章节树，再把每节
正文切成候选知识点。LLM 抽取放在调用方（或后续扩展），这里保证「喂一份大纲，
一定能拿到稳定结构」——纯函数可单测，不受模型配额影响。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import KpNode

#: 章节标题识别（按顺序尝试，越具体的越先匹配）
#: 「第X章/第X单元」是章；「第X节」是节（此前把节也当章，一份教材会被拆成几十个"章"）
_CHAPTER_RE = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百]+\s*[章单元]")
_SECTION_RE = re.compile(
    r"^\s*(第\s*[0-9一二三四五六七八九十百]+\s*节|\d+(?:\.\d+)+|\d+[、.]|"
    r"[（(]\d+[)）]|[一二三四五六七八九十]+、)"
)
_MARKDOWN_RE = re.compile(r"^(#{1,6})\s+(.*)$")

PARSER_BY_TYPE: Dict[str, str] = {
    "textbook": "textbook",
    "outline": "outline",
    "video": "transcript",
    "web": "web",
    "bank": "bank",
    "text": "text",
}


def _slug(text_value: str, fallback: str) -> str:
    """生成稳定的候选 code 后缀：保留字母/数字/中文，其余转下划线。"""
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "_", (text_value or "").strip())
    base = base.strip("_")[:24]
    return base or fallback


def _candidate_code(text_value: str, kind: str, parent_code: str = "") -> str:
    """生成可直接写入 ``kp_node.code`` 的稳定 code（String(32)）。

    2026-11-11 B 端联调修复：此前解析结果只有 ``code_hint``（名称 slug），
    ``CourseIntake.vue`` 却按 ``n.code`` 构造 POST /course/kp-nodes 的 payload，
    导致整批候选 ``code=undefined`` 被 422 拦下、且 ``points`` 层未被遍历。
    这里由后端给每个章/节/点都发一个确定性的短 code：
    - 同一份大纲重复解析 → 同一 code，前端重复导入时 upsert 幂等跳过；
    - 父 code 参与 hash → 不同章节下的同名小节不撞车（KpNode.code 全局唯一）；
    - 只含字母/数字/中文/下划线 → 可直接放进 REST 路径，无需转义。
    """
    slug = _slug(text_value, kind)
    digest = hashlib.sha1(f"{parent_code}/{text_value}".encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"[:32]


def _new_chapter(title: str) -> Dict[str, Any]:
    t = title[:200]
    return {
        "title": t, "code_hint": _slug(t, "ch"),
        "code": _candidate_code(t, "ch"), "sections": [],
    }


def _new_section(title: str, chapter: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    t = title[:200]
    return {
        "title": t, "code_hint": _slug(t, "sec"),
        "code": _candidate_code(t, "sec", (chapter or {}).get("code", "")), "points": [],
    }


def _new_point(name: str, section: Dict[str, Any]) -> Dict[str, Any]:
    t = name[:200]
    return {
        "name": t, "code_hint": _slug(t, "point"),
        "code": _candidate_code(t, "point", section["code"]),
    }


def parse_outline(raw: str, *, source_type: str = "textbook") -> Dict[str, Any]:
    """把原始文本切成 ``章节树 + 候选知识点``。

    输出::

        {"parser": "textbook",
         "chapters": [{"title", "code_hint", "code", "sections": [
             {"title", "code_hint", "code",
              "points": [{"name", "code_hint", "code"}]}]}],
         "chapter_count": n, "point_count": m}

    ``code`` 是给 ``POST /course/kp-nodes`` 直接消费的稳定短码（≤32 字符），
    前端不再自行拼装；旧的 ``code_hint`` 保留兼容其它调用方。
    """
    parser = PARSER_BY_TYPE.get((source_type or "").strip().lower(), "text")
    chapters: List[Dict[str, Any]] = []
    current_ch: Optional[Dict[str, Any]] = None
    current_sec: Optional[Dict[str, Any]] = None
    buffer: List[str] = []

    def flush_points() -> None:
        """把缓冲区里的段落变成候选知识点（每段一句，空行分隔）。"""
        nonlocal buffer
        if current_sec is None:
            buffer = []
            return
        for block in buffer:
            for line in block.splitlines():
                name = line.strip(" \t·-•")
                if not name or len(name) < 2:
                    continue
                current_sec["points"].append(_new_point(name, current_sec))
        buffer = []

    def push_section(title: str) -> None:
        nonlocal current_sec
        flush_points()
        current_sec = _new_section(title, current_ch)
        if current_ch is not None:
            current_ch["sections"].append(current_sec)

    for raw_line in (raw or "").splitlines():
        line = raw_line.rstrip()
        md = _MARKDOWN_RE.match(line)
        if md:
            level, title = len(md.group(1)), md.group(2).strip()
            if level == 1:
                flush_points()
                current_ch = _new_chapter(title)
                chapters.append(current_ch)
                current_sec = None
            else:
                if current_ch is None:
                    current_ch = _new_chapter("（未命名章节）")
                    chapters.append(current_ch)
                push_section(title)
            continue
        if _CHAPTER_RE.match(line):
            flush_points()
            current_ch = _new_chapter(line.strip())
            chapters.append(current_ch)
            current_sec = None
            continue
        if _SECTION_RE.match(line):
            if current_ch is None:
                current_ch = _new_chapter("（未命名章节）")
                chapters.append(current_ch)
            push_section(line.strip())
            continue
        if line.strip():
            if current_sec is None:
                # 无小标题的正文：建立一个「正文」小节，避免整章知识点全丢
                if current_ch is None:
                    current_ch = _new_chapter("（未命名章节）")
                    chapters.append(current_ch)
                push_section("正文")
            buffer.append(line)

    flush_points()
    point_count = sum(len(s["points"]) for ch in chapters for s in ch["sections"])
    return {
        "parser": parser,
        "chapters": chapters,
        "chapter_count": len(chapters),
        "point_count": point_count,
    }


async def upsert_kp_nodes(
    db: AsyncSession,
    *,
    tenant_id: str,
    items: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """M5-3：写入 ``kp_node``；**已存在 code 不覆盖**（可能是教师改过的）。

    新节点一律 ``draft``：抽取结果没经过人工确认，直接进学生图谱会教错。
    """
    from app.services import mastery as mastery_svc

    created = skipped = merged = 0
    codes = []
    for item in items or []:
        code = (item.get("code") or "").strip()
        name = (item.get("name") or "").strip()
        if not code or not name:
            continue

        # 红线：kp_code 写侧唯一入口是 resolve_kp_codes。抽取出的 code 可能
        # 只是「名称 slug」，也可能与已发布 DAG 的 code 不一致；先翻译再落库，
        # 否则会造出第二套键（图谱按 code 查永远为空）。
        resolved = await mastery_svc.resolve_kp_codes(db, [code, name])
        canonical = resolved.get(code) or resolved.get(name)
        if canonical:
            # 已经在 kb_knowledge_points 里：视为已合并，不再建 draft 候选
            merged += 1
            codes.append(canonical)
            continue

        # 尚未进 DAG，但可能已有同名候选（同 code）：不覆盖教师改过的内容
        exists = (
            await db.execute(select(KpNode.id).where(KpNode.code == code))
        ).first()
        if exists:
            skipped += 1
            continue
        db.add(
            KpNode(
                tenant_id=tenant_id,
                code=code,
                course_id=item.get("course_id"),
                parent_code=item.get("parent_code"),
                name=name,
                subject=item.get("subject"),
                grade=item.get("grade"),
                source=item.get("source") or "llm",
                status="draft",
            )
        )
        created += 1
        codes.append(code)
    await db.flush()
    return {
        "created": created, "skipped": skipped, "merged": merged,
        "codes": codes,
    }


async def review_kp_node(
    db: AsyncSession,
    *,
    tenant_id: str,
    code: str,
    status: str,
    reviewer: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """M5-4：审核。published 时同步写入 ``kb_knowledge_points``（图谱数据源）。

    写入用 ``ON CONFLICT DO NOTHING`` 语义（先查后写）：同名 code 已存在时
    不覆盖教研已维护的 title/memory/understanding。
    """
    row = (
        await db.execute(
            select(KpNode).where(KpNode.code == code, KpNode.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if status not in ("draft", "published"):
        raise ValueError("status 只能是 draft/published")
    row.status = status

    if status == "published":
        exists = (
            await db.execute(
                text("SELECT id FROM kb_knowledge_points WHERE code = :c LIMIT 1").bindparams(c=code)
            )
        ).first()
        if exists is None:
            await db.execute(
                text(
                    "INSERT INTO kb_knowledge_points "
                    "(subject, grade, title, content, code, importance) "
                    "VALUES (:s, :g, :t, :c, :code, 'std')"
                ).bindparams(
                    s=row.subject or "数学",
                    g=row.grade,
                    t=row.name,
                    c=row.name,
                    code=code,
                )
            )
    await db.flush()
    return {
        "id": row.id, "code": row.code, "name": row.name, "status": row.status,
        "published_to_kb": status == "published",
        "reviewer": reviewer,
    }