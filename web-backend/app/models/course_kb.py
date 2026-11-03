"""course_kb 题库表模型 + 请求/响应契约。

入库链路：教研产出（Excel/JSON/采购题库）→ parser 清洗为统一字段 →
upsert 到 course_kb。embedding 列预留给 P2 语义检索（/similar）。
"""
from datetime import datetime, timezone
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, field_validator
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    # 0005_tenant_source_tag 迁移补列（跨租户读取 / 隔离脏题用）；ORM 仅做映射，不重建表
    tenant_id = Column(String(64), index=True)
    source_tag = Column(String(32))
    status = Column(String(16), nullable=False, default="draft", server_default="draft")
    reviewed_by = Column(String(64))
    reviewed_at = Column(DateTime(timezone=True))
    # M0-1 教师录题新增列（迁移 0012；create_all 只建缺失表、不加列，线上须手工 ALTER）
    answer_values = Column(JSON)                                    # 标答预展开（M2-4 判分口径）
    choice_type = Column(String(16))                                # single / multi（仅选择题）
    blank_count = Column(Integer)                                   # 填空题空位数
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

    @field_validator("knowledge_points", mode="before")
    @classmethod
    def _null_kp_to_empty(cls, v):
        """存量/导入行 knowledge_points 可能是 NULL，归一为空列表。

        不加这条会让**一条老数据**把整页列表拖成 500（与 created_at=NULL 同类事故）。
        """
        return v or []


class QuestionOut(QuestionIn):
    id: int
    content_hash: str
    # 历史导入行 created_at 可能为 NULL（存量 211 行里有则无值）。
    # 这里放宽容错：列表是展示用途，不该因为一条老数据整页 500。
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CourseKbEmbedding(Base):
    """Agent 侧 RAG 检索表（M0-1 双写目标）。

    与 ``course_kb`` 按 ``content_hash`` 一对一关联，**维度锁死 1024**
    （列类型 ``VECTOR(1024)``，与 qwen3.7-text-embedding 一致）。

    为什么单独建表而不是只写 ``course_kb.embedding``：agent-backend 的
    ``rag.py`` 检索的就是这张表（带 is_demo / source_tag 隔离谓词），
    只写 course_kb.embedding 等于「入库了但检索不到」。

    线上既有表由 ``agent-backend/sql/embeddings.sql`` 建成，本 ORM 仅做映射，
    字段名/类型必须与之逐字对齐；``create_all`` 对已存在的表不改结构。
    """

    __tablename__ = "course_kb_embeddings"

    id = Column(Integer, primary_key=True)
    subject = Column(Text, nullable=False, server_default="math")
    knowledge_point = Column(Text, nullable=False, server_default="")
    # 注意：本表 difficulty 是 TEXT，而 course_kb.difficulty 是 INTEGER，写入须转 str
    difficulty = Column(Text, nullable=False, server_default="")
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, server_default="")
    content = Column(Text, nullable=False)                 # 送检索的文本（题干 + 解析）
    embedding = Column(Vector(1024))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    content_hash = Column(Text, unique=True, index=True)   # 关联 course_kb 唯一键
    kp_code = Column(String(32))
    is_demo = Column(Boolean, nullable=False, default=False, server_default="false")
    source_tag = Column(Text)
    type = Column(Text, nullable=False, server_default="")
    #: M0-4 租户隔离键；RAG 检索必须按此过滤，别校题不得进入本校上下文
    tenant_id = Column(String(32), nullable=False, server_default="school_pilot_01", index=True)


class QuestionCreate(QuestionIn):
    """教师录题请求（M0-1）。

    - ``qtype`` 是前端/方案书用词，与 ``type`` 二选一，``qtype`` 优先；
    - ``choice_type`` 只在选择题上有意义，用于把「选择」细分到「单选/多选」
      （组卷要控题型配比，混成「选择」两件事都做不了）；
    - **不接受 ``status``**：录题一律落 ``draft``，必须走审核接口
      （``POST /exam/questions/{id}/review``）才能发布，审核隔离是红线。
    """

    qtype: Optional[str] = None
    choice_type: Optional[str] = None
    answer_values: Optional[List[str]] = None

    model_config = {"extra": "ignore"}


class QuestionCreateResult(BaseModel):
    """录题结果：单题与批量共用同一形状。"""

    received: int
    created: int
    duplicated: int
    items: List[dict]
    embedding: dict

