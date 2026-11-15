"""④·数字人讲题短视频 API（ROADMAP #28）。

学生端（网关 /api/student/ 透传学生身份）：
- GET  /explanation-video/by-question?question=...   查当前租户该题是否有可看视频

教师 / 后台（网关 /api/admin/，需角色）：
- POST /explanation-video/generate   触发 / 复用生成任务（幂等）
- POST /explanation-video/regen      重新生成（done / failed 也强制重跑）
- GET  /explanation-video/config     可用形象 / 音色列表 + 总开关
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import business_tenant_id, get_current_user, require_role
from app.models.explanation_video import ExplanationVideo
from app.services.video_generator import content_hash, run_generation

student_router = APIRouter(tags=["explanation-video"])
admin_router = APIRouter(tags=["explanation-video-admin"])


def _row_out(row: ExplanationVideo) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "video_url": row.video_url,
        "duration_sec": row.duration_sec,
        "error": row.error,
        "content_hash": row.content_hash,
    }


@student_router.get("/explanation-video/by-question")
async def by_question(
    question: str = Query(..., min_length=1),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    tid = business_tenant_id(user)
    h = content_hash(question)
    row = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.content_hash == h,
            )
            .order_by(ExplanationVideo.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        return {
            "status": "none",
            "video_url": None,
            "duration_sec": None,
            "error": None,
            "content_hash": h,
        }
    return _row_out(row)


# ------------------------- 教师 / 后台端 -------------------------

class GenerateIn(BaseModel):
    question: str = Field(..., min_length=1)
    knowledge_point: Optional[str] = None
    # 讲题台词稿；不传则退化为题目文本本身
    script: Optional[str] = None
    avatar_id: Optional[str] = "default"
    voice_id: Optional[str] = "default"


@admin_router.get("/explanation-video/config")
async def config(user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER))):
    from app.core.config import settings

    return {
        "enabled": settings.digital_human_enabled,
        "avatars": [{"id": "default", "name": "默认讲师（占位）"}],
        "voices": [{"id": "default", "name": "默认音色（占位）"}],
        "note": "真·数字人形象 / 音色需在 GPU 服务器部署 CosyVoice + SadTalker/Wav2Lip 后接入",
    }


def _require_enabled() -> None:
    from app.core.config import settings

    if not settings.digital_human_enabled:
        raise HTTPException(status_code=403, detail="数字人管线未启用（digital_human_enabled=false）")


@admin_router.post("/explanation-video/generate")
async def generate(
    body: GenerateIn,
    background: BackgroundTasks,
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    _require_enabled()
    tid = business_tenant_id(user)
    h = content_hash(body.question)
    row = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.content_hash == h,
            )
            .order_by(ExplanationVideo.created_at.desc())
        )
    ).scalar_one_or_none()

    if row is not None and row.status in ("pending", "processing", "done"):
        return _row_out(row)  # 幂等复用
    if row is None:
        row = ExplanationVideo(
            tenant_id=tid,
            content_hash=h,
            question_text=body.question,
            knowledge_point=body.knowledge_point,
            script=body.script or body.question,
            avatar_id=body.avatar_id,
            voice_id=body.voice_id,
            status="pending",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    else:  # failed → 复位重试
        row.status = "pending"
        row.error = None
        row.script = body.script or row.script or body.question
        await db.commit()

    background.add_task(run_generation, row.id)
    return _row_out(row)


@admin_router.post("/explanation-video/regen")
async def regen(
    body: GenerateIn,
    background: BackgroundTasks,
    user: dict = Depends(require_role(Role.ADMIN, Role.REGION_ADMIN, Role.TEACHER)),
    db=Depends(get_db),
):
    _require_enabled()
    tid = business_tenant_id(user)
    h = content_hash(body.question)
    row = (
        await db.execute(
            select(ExplanationVideo)
            .where(
                ExplanationVideo.tenant_id == tid,
                ExplanationVideo.content_hash == h,
            )
            .order_by(ExplanationVideo.created_at.desc())
        )
    ).scalar_one_or_none()
    if row is None:
        row = ExplanationVideo(
            tenant_id=tid,
            content_hash=h,
            question_text=body.question,
            knowledge_point=body.knowledge_point,
            script=body.script or body.question,
            avatar_id=body.avatar_id,
            voice_id=body.voice_id,
            status="pending",
        )
        db.add(row)
    else:
        row.status = "pending"
        row.error = None
        row.script = body.script or row.script or body.question
    await db.commit()
    await db.refresh(row)
    background.add_task(run_generation, row.id)
    return _row_out(row)
