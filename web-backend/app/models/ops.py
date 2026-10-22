"""B 端运营实体 ORM 模型：知识点 / 错题 / 批改 / 合规。

与 course_kb 共用同一个 Base，启动时一次 create_all 全部建出。
2026-10-23：这四块原先是 API 层的内存 _STORE 占位，现改为真实持久化（PostgreSQL）。
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgePoint(Base):
    """知识点（教研内容管理）。"""

    __tablename__ = "kb_knowledge_points"

    id = Column(Integer, primary_key=True)
    subject = Column(String(32), nullable=False, index=True)
    grade = Column(String(32), index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class WrongQuestion(Base):
    """错题本。"""

    __tablename__ = "wrong_questions"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    question = Column(Text, nullable=False)
    wrong_answer = Column(Text)
    correct_answer = Column(Text)
    knowledge_point = Column(String(128), index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Submission(Base):
    """学生作答与教师批改。"""

    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    answer = Column(Text, nullable=False)
    score = Column(Float)
    comment = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ComplianceItem(Base):
    """合规审核项。"""

    __tablename__ = "compliance_items"

    id = Column(Integer, primary_key=True)
    type = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
