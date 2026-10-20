from fastapi import APIRouter, File, UploadFile

from app.models.schemas import SolveRequest, SolveResponse, SimilarRequest
from app.core import orchestrator

router = APIRouter(prefix="/api/app", tags=["solve"])


@router.post("/solve", response_model=SolveResponse)
async def solve(req: SolveRequest):
    """文本题：解题 + 分步讲解 + 触类旁通"""
    return await orchestrator.handle_question(
        req.question, mode=req.mode, subject=req.subject
    )


@router.post("/solve_image")
async def solve_image(file: UploadFile = File(...)):
    """拍照题：OCR 识别 -> 解题"""
    data = await file.read()
    return await orchestrator.handle_image(data)


@router.post("/similar", response_model=SolveResponse)
async def similar(req: SimilarRequest):
    """触类旁通：基于题干生成相似/变式题"""
    return await orchestrator.handle_question(
        req.question, mode="similar", subject=req.subject
    )
