"""B 端运营实体 ORM 模型：知识点 / 错题 / 批改 / 合规。

与 course_kb 共用同一个 Base，启动时一次 create_all 全部建出。
2026-10-23：这四块原先是 API 层的内存 _STORE 占位，现改为真实持久化（PostgreSQL）。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

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


class StudentEvent(Base):
    """学情事件流水（来自 agent-backend 上报的 student_event）。"""

    __tablename__ = "student_events"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), index=True)
    event_type = Column(String(32), nullable=False, index=True)  # solve / solve_image / similar
    subject = Column(String(32))
    mode = Column(String(16))
    question = Column(Text)
    knowledge_points = Column(JSON, default=list)
    occurred_at = Column(DateTime(timezone=True))  # 事件原时间
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class StudentMastery(Base):
    """知识点掌握度（按 学生×知识点 聚合）。

    说明：当前事件不含判定结果，故只累计练习量；
    掌握度 mastery = correct/(correct+wrong)，待接入判分后再更新对错计数。
    """

    __tablename__ = "student_mastery"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), index=True)
    subject = Column(String(32))
    knowledge_point = Column(String(128), nullable=False, index=True)
    practice_count = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    last_practiced_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("student_id", "knowledge_point", name="uq_mastery_student_kp"),
    )
