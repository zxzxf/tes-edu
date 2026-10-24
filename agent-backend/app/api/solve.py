from fastapi import APIRouter, File, Request, UploadFile

from app.core import events, orchestrator
from app.models.schemas import SolveRequest, SolveResponse, SimilarRequest

router = APIRouter(prefix="/api/app", tags=["solve"])


def _identity(request: Request) -> dict:
    """取网关注入的身份（X-User-Id / X-Tenant-Id 由网关无条件覆盖写，可信）。"""
    if request is None:
        return {}
    return {
        "student_id": request.headers.get("X-User-Id") or "",
        "tenant_id": request.headers.get("X-Tenant-Id") or "",
    }


@router.post("/solve", response_model=SolveResponse)
async def solve(req: SolveRequest, request: Request):
    """文本题：解题 + 分步讲解 + 触类旁通（T-C1 起按意图三档路由，T-C2 起支持 Hint 阶梯）"""
    result = await orchestrator.handle_question(
        req.question,
        mode=req.mode,  # None -> 意图三档路由；显式值 -> 尊重调用方
        subject=req.subject,
        student_answer=req.student_answer,
        hint=req.hint,
        student_reply=req.student_reply,
        direct_reveal=req.direct_reveal,
        student_id=_identity(request).get("student_id") or "",
    )
    # T-C3 "直接看解析"是一等功能：mode=direct 直达 L4。
    # 行为入埋点且不作为违规（R6）——与"挫败两次被动拿到 L4"区分，
    # T-C4 聚合时 direct_reveal 不计入首轮直给率分子。
    is_direct = req.mode == "direct" or bool(result.get("hint_direct_reveal"))
    if req.hint or is_direct:
        if req.hint:
            # T-C2：Hint 阶梯轮事件（T-C4 埋点聚合推进率/挫败率的数据源）。
            # 旧消费者忽略未知 event_type；本事件不替代 solve 事件的练习量统计。
            events.emit_student_event_bg(
                {
                    "event_type": "hint_shown",
                    **_identity(request),
                    "subject": req.subject or "",
                    # T-D3：本轮 Hint 是否由模板降级（LLM 不可用/输出违约）
                    "degraded": result.get("degraded"),
                    "mode": result.get("mode") or "",
                    "intent": result.get("intent") or "",
                    "intent_source": result.get("intent_source") or "",
                    "question": req.question,
                    "knowledge_points": result.get("knowledge_points") or [],
                    "content_hash": result.get("content_hash") or "",
                    # 阶梯状态：等级/连击/是否压轴/是否直达 L4
                    "hint_level": result.get("hint_level"),
                    "hint_level_name": result.get("hint_level_name") or "",
                    "hint_frustration_streak": result.get("hint_frustration_streak"),
                    "hint_finale": result.get("hint_finale"),
                    "hint_direct_reveal": result.get("hint_direct_reveal"),
                    "student_reply": req.student_reply,
                    # T-C3：题型路由 + 分步进度（埋点按题型分层聚合）
                    "question_tier": result.get("question_tier") or "",
                    "hint_max_rounds": result.get("hint_max_rounds"),
                    "hint_rounds": result.get("hint_rounds"),
                    "hint_step": result.get("hint_step"),
                    "hint_step_total": result.get("hint_step_total"),
                }
            )
        if is_direct:
            # T-C3：直接看解析点击事件（App 常驻按钮 + 一次轻确认后的直达轮）。
            # 与 hint_shown 并列而非合并：T-C4 六项指标要把"主动看解析"
            # 与"阶梯内挫败到顶"分开统计（R6：前者是功能使用，不是教学事故）。
            events.emit_student_event_bg(
                {
                    "event_type": "direct_reveal",
                    **_identity(request),
                    "subject": req.subject or "",
                    "degraded": result.get("degraded"),
                    "mode": result.get("mode") or "",
                    "intent": result.get("intent") or "",
                    "question": req.question,
                    "knowledge_points": result.get("knowledge_points") or [],
                    "content_hash": result.get("content_hash") or "",
                    # 点击前的阶梯状态：轮次越多越说明"引导无效主动退出"，
                    # 轮次越少越可能是"赶时间"——T-C4 分流分析的依据
                    "hint_level": result.get("hint_level"),
                    "hint_level_name": result.get("hint_level_name") or "",
                    "hint_rounds": result.get("hint_rounds"),
                    "hint_finale": result.get("hint_finale"),
                    "question_tier": result.get("question_tier") or "",
                    "question_tier_name": result.get("question_tier_name") or "",
                    "hint_step": result.get("hint_step"),
                    "hint_step_total": result.get("hint_step_total"),
                    "student_reply": req.student_reply,
                }
            )
        # 阶梯轮中提交了作答：补发 solve 事件，保持练习量/判分统计不中断
        if req.student_answer is None:
            return result
    judgement = result.get("judgement") or {}
    events.emit_student_event_bg(
        {
            "event_type": "solve",
            **_identity(request),
            "subject": req.subject or "",
            # T-C1：记录实际生效的档位与意图来源（rules/llm/default/explicit），
            # 供 T-C4 六项指标（首轮直给率等）聚合；旧消费者忽略新字段
            "mode": result.get("mode") or "",
            "intent": result.get("intent") or "",
            "intent_source": result.get("intent_source") or "",
            "question": req.question,
            "knowledge_points": result.get("knowledge_points") or [],
            # 命中的标注题：把归因挂到具体题目（缺了它 student_step_errors 无法归属）
            "content_hash": result.get("content_hash") or "",
            # T-D3：主解是否降级为模板通法（用量表另有 llm_usage 明细，此处供埋点聚合）
            "degraded": result.get("degraded"),
            # 判分结果：未提交作答时为 None（消费端只累计练习量）
            "correct": judgement.get("correct"),
            "partial": judgement.get("partial"),
            # S3 步骤级归因载荷：向后兼容，旧消费者忽略新字段
            "first_wrong_step": judgement.get("first_wrong_step"),
            "error_type": judgement.get("error_type"),
            "attribution_source": judgement.get("attribution_source"),
            "evidence": judgement.get("evidence"),
            # S4 过程缺失标记（need_process 时 correct 为 None，消费端双保险防误记错题）
            "need_process": judgement.get("need_process"),
            # 学生原始作答：错题本 wrong_answer 优先用它（比 evidence 片段更完整）
            "student_answer": req.student_answer,
        }
    )
    return result


@router.post("/solve_image")
async def solve_image(file: UploadFile = File(...), request: Request = None):
    """拍照题：OCR 识别 -> 解题"""
    data = await file.read()
    result = await orchestrator.handle_image(data)
    events.emit_student_event_bg(
        {
            "event_type": "solve_image",
            **_identity(request),
            "question": result.get("recognized_text") or "",
            "knowledge_points": result.get("knowledge_points") or [],
        }
    )
    return result


@router.post("/similar", response_model=SolveResponse)
async def similar(req: SimilarRequest, request: Request):
    """触类旁通：基于题干生成相似/变式题"""
    result = await orchestrator.handle_question(
        req.question, mode="similar", subject=req.subject
    )
    events.emit_student_event_bg(
        {
            "event_type": "similar",
            **_identity(request),
            "subject": req.subject or "",
            "question": req.question,
            "knowledge_points": result.get("knowledge_points") or [],
        }
    )
    return result
