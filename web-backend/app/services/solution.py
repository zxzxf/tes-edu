"""M3-1/M3-2 卷内题解析生成与缓存。

三条硬约束：

1. **幂等**：同一 ``(paper_id, paper_question_id)`` 只生成一次。教师连点、学生刷新
   都不该重复烧 token；并发下靠唯一约束兜底（第二个请求读到已存在行就返回）。
2. **提交前不可见（学生）**：防抄。权限判断在 API 层，本模块只负责取/生成，
   但明确接受 ``allow_generate`` 开关，避免「以为拦住了其实早生成好了」。
3. **数值一致（M2-11）**：生成结果必须与卷面标答一致；不一致的解析**不入缓存**
   （否则错误解析会被永久固化），返回降级说明。

LLM 调用失败一律返回 ``ok=False`` + 原因，不抛 500——解析是增值服务，
挂了不该把取卷/成绩页一起拖垮。

2026-11-17（502 修复）：LLM 超时从 40s 降到 ``SOLUTION_LLM_TIMEOUT``（22s）。
根因是 Cloudflare 免费隧道边缘 30s 硬超时，而冷启动解析生成要 30s+，
边缘先掐断 → 网关记 context canceled → 502。22s 留出余量保证在边缘超时前返回；
模型优先用 BYOM ``model_simple``（非思考快模型），失败则返回**兜底解析**
（ok=True + degraded=True，明确标注"详细解析生成中"），绝不让用户干等或看到报错。
治本靠交卷后异步预热（见 warmup_paper_solutions）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.ops import ExamPaperQuestion, ExamSolution
from app.services import byom

log = logging.getLogger(__name__)

#: 解析提示词版本：与 agent-backend 的 PROMPT_VERSION 同口径 bump。
#: 只有「数值一致」这一条硬约束写在这里；更细的教学规则在 agent 侧。
SOLUTION_PROMPT_VERSION = 4  # v4：快模型 + 短超时 + 兜底解析

#: LLM 调用超时（秒）。必须显著小于 Cloudflare 隧道边缘 30s 硬超时，
#: 留出 DB 落库/序列化的余量，避免 502。可用环境变量覆盖以便灰度。
SOLUTION_LLM_TIMEOUT = float(os.getenv("SOLUTION_LLM_TIMEOUT", "22"))

#: 兜底解析的提示文案（不编造解题步骤，明确告知在生成中）。
FALLBACK_STEP = "详细解析正在生成中，标准答案以卷面为准，可稍后刷新查看。"

SOLUTION_SYSTEM = (
    "你是一名高中数学老师，为学生写**解析**（正确解法 + 分步推导）。\n"
    "【数值一致性·硬性】本题给出的【标准答案】是唯一结论：不得输出与它冲突的数值，"
    "包括「约为」「即」「等于」后面的任何换算值；若标准答案是分数而要给小数，必须自己算准，"
    "算不准就只写分数。标准答案缺失时明确写「标准答案待核对」，不要自行编答案。\n"
    "多解题（±2、2 或 -2）必须全部列出，不得只挑一个。\n"
    "只输出严格 JSON，不要包裹 markdown："
    '{"answer": "最终答案", "steps": ["第1步…", "第2步…"]}。'
)


def _get(obj: Any, key: str, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _options_text(options: Any) -> str:
    if isinstance(options, dict):
        return "\n".join(f"{k}. {v}" for k, v in options.items())
    return ""


def build_prompt(question: Any) -> str:
    qtype = _get(question, "qtype", "") or ""
    stem = (_get(question, "stem", "") or "")[:2000]
    answer = (_get(question, "answer", "") or "").strip()
    opts = _options_text(_get(question, "options"))
    return (
        f"【题型】{qtype}\n【题干】{stem}\n"
        + (f"【选项】\n{opts}\n" if opts else "")
        + f"【标准答案】{answer or '（缺失）'}\n"
        "请给出解析。"
    )


def _normalize_steps(payload: Any) -> Optional[Dict[str, Any]]:
    """把模型输出收敛成 ``{"answer": str, "steps": [str]}``；解析不出来返回 None。"""
    if not isinstance(payload, dict):
        return None
    steps = payload.get("steps")
    if isinstance(steps, str):
        steps = [steps]
    if not isinstance(steps, list):
        return None
    clean = [str(s).strip() for s in steps if str(s or "").strip()]
    if not clean:
        return None
    return {"answer": str(payload.get("answer") or "").strip(), "steps": clean[:20]}


async def _call_llm(db: AsyncSession, tenant_id: str, question: Any) -> Optional[Dict[str, Any]]:
    cfg = await byom.get_config(db, tenant_id)
    if cfg is None or not cfg.base_url or not cfg.api_key_enc or not cfg.enabled:
        return None
    try:
        import json

        import httpx

        api_key = byom.decrypt_api_key(cfg.api_key_enc)
        # 快模型优先：BYOM 配了 model_simple（非思考快模型，如 qwen3.6-flash）
        # 就用它，冷启动生成从 30s+ 压到数秒，避免 Cloudflare 30s 边缘超时。
        # 没配则回退到 cfg.model，保证老租户行为不变。
        model = getattr(cfg, "model_simple", None) or cfg.model
        async with httpx.AsyncClient(timeout=SOLUTION_LLM_TIMEOUT) as client:
            resp = await client.post(
                cfg.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SOLUTION_SYSTEM},
                        {"role": "user", "content": build_prompt(question)},
                    ],
                    "temperature": 0,
                    "max_tokens": 1200,
                    # 2026-11-17：qwen3.x-flash 默认开启思考链（reasoning_content），
                    # 实测解析请求带思考要 35s、关掉只要 10s。关思考是 502 的关键修复：
                    # 解析是结构化 JSON 输出，不需要模型"想太久"。非兼容端点忽略此字段。
                    "enable_thinking": False,
                },
            )
        if resp.status_code != 200:
            log.warning("solution llm http %s", resp.status_code)
            return None
        text = (
            (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        return _normalize_steps(json.loads(text))
    except Exception as exc:  # noqa: BLE001 解析失败不影响取卷
        log.warning("solution llm failed: %s", exc)
        return None


def fallback_solution(question: Any) -> Dict[str, Any]:
    """LLM 不可用时的兜底解析：只回卷面标答 + 明确提示，不编造步骤。

    - 数值一定与标答一致（直接用卷面 answer），不会踩 M2-11 冲突红线；
    - ``degraded=True`` 让前端标注"解析生成中"，不谎称是完整解析；
    - 调用方**不落缓存**，下次访问会重新尝试生成真解析。
    """
    answer = (_get(question, "answer", "") or "").strip()
    return {
        "answer": answer or "标准答案待核对",
        "steps": [FALLBACK_STEP],
    }


def numeric_conflict(answer: str, solution: Dict[str, Any]) -> List[str]:
    """解析结论与卷面标答的数值冲突（M2-11）。

    只做**保守**比对：两边都能抽到「结论性数值」且完全不等才判冲突；抽不到就放过
    （宁可漏报，也不能把表述差异当冲突把好解析拦掉）。
    """
    from app.core.equiv import to_number

    ref = to_number(answer or "")
    if ref is None:
        return []
    final = to_number(str(solution.get("answer") or ""))
    if final is None:
        # answer 字段没给数值时，看最后一步是否只有一个数（常见写法）
        final = to_number(solution["steps"][-1])
    if final is None:
        return []
    if abs(ref - final) <= 1e-9:
        return []
    return [f"解析结论 {solution.get('answer') or solution['steps'][-1]} 与卷面标答 {answer} 不一致"]


async def get_cached(
    db: AsyncSession, paper_id: int, paper_question_id: int
) -> Optional[ExamSolution]:
    return (
        await db.execute(
            select(ExamSolution).where(
                ExamSolution.paper_id == paper_id,
                ExamSolution.paper_question_id == paper_question_id,
            )
        )
    ).scalar_one_or_none()


def out_of(row: ExamSolution, *, cached: bool = True) -> Dict[str, Any]:
    return {
        "paper_id": row.paper_id,
        "question_id": row.paper_question_id,
        "solution_steps": row.solution_steps or {"answer": "", "steps": []},
        "cached": cached,
        "ok": True,
        "prompt_version": row.prompt_version,
        "model": row.model,
        "generated_at": row.generated_at.isoformat() if row.generated_at else None,
    }


async def get_or_generate(
    db: AsyncSession,
    *,
    tenant_id: str,
    paper_id: int,
    question: ExamPaperQuestion,
    allow_generate: bool = True,
) -> Dict[str, Any]:
    """取解析；没有且允许生成时调 LLM 并落库（幂等）。"""
    cached = await get_cached(db, paper_id, question.id)
    if cached is not None:
        return out_of(cached)
    if not allow_generate:
        return {
            "paper_id": paper_id,
            "question_id": question.id,
            "solution_steps": None,
            "cached": False,
            "ok": False,
            "reason": "解析尚未生成（提交前不允许生成/查看）",
        }

    solution = await _call_llm(db, tenant_id, question)
    if solution is None:
        # 兜底：给一个基于卷面标答的最小解析，让结果页有内容、不卡在加载/报错。
        # 不落缓存（下面直接 return），下次访问会再试真解析；prewarm 也会补上。
        return {
            "paper_id": paper_id,
            "question_id": question.id,
            "solution_steps": fallback_solution(question),
            "cached": False,
            "ok": True,
            "degraded": True,
            "reason": "详细解析生成中，当前仅显示卷面标准答案（稍后刷新获取完整解析）",
        }

    conflicts = numeric_conflict(question.answer or "", solution)
    if conflicts:
        # 不入缓存：错误解析一旦固化，之后所有人都拿到错的
        return {
            "paper_id": paper_id,
            "question_id": question.id,
            "solution_steps": solution,
            "cached": False,
            "ok": False,
            "degraded": True,
            "reason": "解析与卷面标答不一致，已丢弃（不入缓存）",
            "conflicts": conflicts,
        }

    row = ExamSolution(
        tenant_id=tenant_id,
        paper_id=paper_id,
        paper_question_id=question.id,
        solution_steps=solution,
        prompt_version=SOLUTION_PROMPT_VERSION,
        model="",
    )
    try:
        # SAVEPOINT：并发下另一请求已写入同一 (paper_id, question_id) 时，
        # 只回滚这一条，不让整个会话进入 aborted 状态。
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await get_cached(db, paper_id, question.id)
        if existing is not None:
            # 后到者让先到者的解析生效：两边同源，没必要覆盖。
            return out_of(existing)
        raise
    # 首次生成：cached=False，前端可区分「刚生成」与「读缓存」
    return out_of(row, cached=False)


# --------------------------------------------------------------------------- 异步预热
async def warmup_paper_solutions(
    paper_id: int,
    tenant_id: str,
    *,
    session_factory=None,
    max_questions: int = 20,
) -> int:
    """交卷后台预热：把本卷各题解析提前生成入缓存，学生点开时秒回。

    设计要点：
    - **自建会话**：在 BackgroundTasks 里跑，请求会话已关闭，不能复用；
    - **只补缺**：已有缓存的题跳过（get_or_generate 内部先查缓存），不重复烧 token；
    - **串行**：避免并发出题打爆 LLM 端点触发 429；交卷后没有用户等待，慢点没关系；
    - **全程吞异常**：预热是增值服务，任何失败都不影响已提交的成绩；
    - **有上限**：max_questions 防止超大卷无限生成。

    :return: 本次新生成解析的题数。
    """
    factory = session_factory or _default_session_factory()
    if factory is None:
        return 0
    made = 0
    try:
        async with factory() as db:
            qs = (
                await db.execute(
                    select(ExamPaperQuestion)
                    .where(ExamPaperQuestion.paper_id == paper_id)
                    .order_by(ExamPaperQuestion.seq)
                    .limit(max_questions)
                )
            ).scalars().all()
            for q in qs:
                try:
                    existing = await get_cached(db, paper_id, q.id)
                    if existing is not None:
                        continue
                    result = await get_or_generate(
                        db, tenant_id=tenant_id, paper_id=paper_id, question=q
                    )
                    # 只有真正生成并落库（非兜底）才算成功；兜底不落缓存，
                    # 保持 ok=True 但 degraded=True，下次仍会重试。
                    if result.get("ok") and not result.get("degraded"):
                        await db.commit()
                        made += 1
                    else:
                        await db.rollback()  # 丢弃本次未落库的改动，继续下一题
                except Exception as exc:  # noqa: BLE001 单题失败不影响整卷预热
                    log.warning("warmup solution failed: paper=%s q=%s: %s", paper_id, q.id, exc)
                    try:
                        await db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as exc:  # noqa: BLE001 预热整体失败也不抛出
        log.warning("warmup paper solutions failed: paper=%s: %s", paper_id, exc)
    return made


def _default_session_factory():
    """取默认 DB 会话工厂；导入延后，避免模块加载期循环依赖。"""
    try:
        from app.core.db import SessionLocal

        return SessionLocal
    except Exception:  # noqa: BLE001
        return None