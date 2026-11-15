"""④·数字人讲题短视频（ROADMAP #28）核心表。

讲解文案成熟后，由教师 / 后台触发生成任务：TTS（本地 / 百炼）→ 数字人驱动 →
ffmpeg 合成 → 产出 mp4，落本地存储（MinIO 未接入前的过渡）并经静态路由对外提供。
状态机：pending → processing → done | failed（failed 可重新触发）。
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, String, Float, Text, DateTime, Index
from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExplanationVideo(Base):
    __tablename__ = "explanation_videos"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id = Column(String(64), nullable=False)
    # 题干归一化后的 sha256（取前 16 位即可，碰撞概率可忽略且便于排障）
    content_hash = Column(String(64), nullable=False)
    question_text = Column(Text, nullable=False)
    knowledge_point = Column(String(255), nullable=True)
    # 讲题台词稿（answer + steps 拼合）；生成时落库，便于重看 / 审计
    script = Column(Text, nullable=True)
    tts_engine = Column(String(64), nullable=True)
    avatar_id = Column(String(64), nullable=True)
    voice_id = Column(String(64), nullable=True)
    video_url = Column(String(1024), nullable=True)
    duration_sec = Column(Float, nullable=True)
    # pending | processing | done | failed
    status = Column(String(16), nullable=False, default="pending")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_explanation_videos_tenant_hash", "tenant_id", "content_hash"),
    )
