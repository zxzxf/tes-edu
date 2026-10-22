"""course_kb 题库表模型 + 请求/响应契约。

入库链路：教研产出（Excel/JSON/采购题库）→ parser 清洗为统一字段 →
upsert 到 course_kb。embedding 列预留给 P2 语义检索（/similar）。
"""
from datetime import datetime, timezone
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class CourseQuestion(Base):
    __tablename__ = "course_kb"

    id = Column(Integer, primary_key=True)
    subject = Column(String(32), nullable=False, index=True)       # 学科：数学/英语...
    grade = Column(String(32), index=True)                          # 年级/学段：高一
    chapter = Column(String(64))                                    # 章节
    type = Column(String(16), index=True)                           # 选择/填空/解答/压轴
    stem = Column(Text, nullable=False)                             # 题干
    options = Column(JSON)                                          # 选择题选项 list[str]
    answer = Column(Text)                                           # 答案
    analysis = Column(Text)                                         # 解析
    difficulty = Column(Integer, default=3)                         # 1~5
    paper_type = Column(String(32))                                 # 卷别：新高考I卷
    source_province = Column(String(32))                            # 来源省份
    knowledge_points = Column(JSON)                                 # 知识点标签 list[str]
    content_hash = Column(String(64), unique=True, index=True)       # 去重指纹
    # 维度须与所用 embedding 模型一致：当前 qwen3.7-text-embedding / text-embedding-v4 均为 1024
    embedding = Column(Vector(1024))
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (UniqueConstraint("content_hash", name="uq_course_kb_hash"),)


class QuestionIn(BaseModel):
    subject: str
    grade: Optional[str] = None
    chapter: Optional[str] = None
    type: str = "解答"
    stem: str
    options: Optional[List[str]] = None
    answer: Optional[str] = None
    analysis: Optional[str] = None
    difficulty: int = 3
    paper_type: Optional[str] = None
    source_province: Optional[str] = None
    knowledge_points: List[str] = []


class QuestionOut(QuestionIn):
    id: int
    content_hash: str
    created_at: datetime

    model_config = {"from_attributes": True}
