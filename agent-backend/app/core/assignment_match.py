"""M1-8 拍照入口收敛：把「这张图是哪道作业题」认出来。

拍照识题原本是一条**自由解题**入口：拍什么就解什么、还能顺便判分。
作业闭环上线后这条路径必须收敛（方案书 A9/R1）：

* 拍照只服务**已下发的作业题**；匹配不到就提示「本题不在你的作业中」；
* 不进入判分——自由文本/OCR 失真会把不确定性带进判分链路；
* 但讲解/图谱/错题集保留，所以拒绝时要给出可走的出口，不是把功能关死。

匹配策略（保守优先）：
1. 两边都做**强归一**（去空白/标点/全半角、LaTeX 分式压成 a/b、上标压成数字），
   等价即命中；
2. 归一后仍不等价时，用 **difflib 相似度**兜底——OCR 偶发错字（``f(x)=x²+1``
   认成 ``f(x)=x2+l``）不应直接拒掉。阈值默认 0.88，可用
   ``PHOTO_MATCH_THRESHOLD`` 调整；
3. 多个题都过阈值时取**最相似**的一条；同分不猜，返回 None（宁可让学生重拍）。
"""
from __future__ import annotations

import difflib
import os
import re
import unicodedata
from typing import Any, Iterable, List, Optional

DEFAULT_THRESHOLD = float(os.getenv("PHOTO_MATCH_THRESHOLD", "0.88"))

_PUNCT_RE = re.compile(r"[\s，,。.;；:：、!！?？\"'“”‘’`~·\-—_（）()\[\]【】{}<>《》]+")
_FRAC_RE = re.compile(r"\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}")
_NEWLINE_RE = re.compile(r"\s+")


def normalize_stem(text: Any) -> str:
    """OCR/题库两侧共用的归一：只抹平排版噪声，不动数学语义。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = s.replace("$", "")
    s = _FRAC_RE.sub(r"\1/\2", s)
    s = s.replace("\\left", "").replace("\\right", "")
    # 上标/下标数字：x² -> x2、a₁ -> a1（OCR 常把角标压成普通字符）
    s = s.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789"))
    s = s.lower()
    s = _PUNCT_RE.sub("", s)
    return s


def similarity(a: Any, b: Any) -> float:
    """归一后的相似度（0~1）。任一侧为空返回 0。"""
    na, nb = normalize_stem(a), normalize_stem(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def stems_match(a: Any, b: Any, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return similarity(a, b) >= threshold


def best_match(
    stem: Any,
    questions: Iterable[dict],
    threshold: float = DEFAULT_THRESHOLD,
) -> Optional[int]:
    """在作业题里找最相似的一道，返回其 ``seq``；没有可信匹配返回 None。

    同分（两道题相似度完全一样）时**不猜**：返回 None 让学生重拍，
    比把答案挂到隔壁题上安全得多。
    """
    scored: List[tuple] = []
    for q in questions or []:
        seq = q.get("seq") if isinstance(q, dict) else getattr(q, "seq", None)
        text = q.get("stem") if isinstance(q, dict) else getattr(q, "stem", "")
        if seq is None:
            continue
        s = similarity(stem, text)
        if s >= threshold:
            scored.append((s, seq))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
        return None
    return scored[0][1]


async def load_assignment_questions(
    *, tenant_id: str, student_id: str
) -> List[dict]:
    """取该学生**当前可做/已下发**的作业题（seq + stem + 本卷答案）。

    这是 web-backend 的库，agent 侧直连 PostgreSQL 只读。DB 未配置或查不到时
    返回空列表——调用方据此走"不在作业中"分支，不会因为查库失败就放行自由判分。
    """
    from app.core import config

    if not config.db_ready():
        return []
    try:
        from app.db.pg import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pq.seq, pq.stem, pq.answer
                FROM exam_assignments a
                JOIN exam_papers p ON p.id = a.paper_id AND p.published = TRUE
                JOIN exam_paper_questions pq ON pq.paper_id = p.id
                WHERE a.tenant_id = $1
                  AND (
                        (a.target_type = 'student' AND a.target_student = $2)
                     OR (a.target_type = 'grade')
                  )
                ORDER BY a.id DESC, pq.seq
                """,
                tenant_id,
                student_id,
            )
        return [
            {"seq": int(r["seq"]), "stem": r["stem"] or "", "answer": r["answer"] or ""}
            for r in rows
        ]
    except Exception:  # noqa: BLE001 查库失败按"没有作业"处理（fail-closed）
        return []
