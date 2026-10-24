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
    """文本题：解题 + 分步讲解 + 触类旁通"""
    result = await orchestrator.handle_question(
        req.question,
        mode=req.mode or "explain",
        subject=req.subject,
        student_answer=req.student_answer,
    )
    judgement = result.get("judgement") or {}
    events.emit_student_event_bg(
        {
            "event_type": "solve",
            **_identity(request),
            "subject": req.subject or "",
            "mode": req.mode or "explain",
            "question": req.question,
            "knowledge_points": result.get("knowledge_points") or [],
            # 命中的标注题：把归因挂到具体题目（缺了它 student_step_errors 无法归属）
            "content_hash": result.get("content_hash") or "",
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
