"""Pydantic 数据模型（请求/响应）。

P0/P2 阶段先定义契约，真实持久化在接入 PostgreSQL 后补齐。
"""
from typing import List, Optional

from pydantic import BaseModel


# ---------- 认证 ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class UserInfo(BaseModel):
    user_id: str
    role: str


# ---------- 模块1：教研内容管理 ----------
class KnowledgePoint(BaseModel):
    id: Optional[int] = None
    subject: str          # 学科，如 数学/英语
    grade: str            # 年级，如 初三
    title: str
    content: str
    tags: List[str] = []

    model_config = {"from_attributes": True}


# ---------- 模块2：学情看板 ----------
class AnalyticsSummary(BaseModel):
    student_id: str
    weak_points: List[str] = []
    mastery: float = 0.0          # 掌握度 0~1
    trend: List[float] = []       # 近期掌握度趋势


# ---------- 模块3：错题本 ----------
class WrongQuestion(BaseModel):
    id: Optional[int] = None
    student_id: str
    question: str
    wrong_answer: str
    correct_answer: str
    knowledge_point: str

    model_config = {"from_attributes": True}


# ---------- 模块4：自动组卷 ----------
class ExamPaper(BaseModel):
    id: Optional[int] = None
    title: str
    knowledge_points: List[str] = []
    difficulty: int = 3           # 1~5
    questions: List[str] = []


# ---------- 模块5：教师批改 ----------
class Submission(BaseModel):
    id: Optional[int] = None
    student_id: str
    answer: str
    score: Optional[float] = None
    comment: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------- 模块6：家长周报 ----------
class WeeklyReport(BaseModel):
    """学情周报（真实数据，来源 student_mastery + student_events）。"""

    student_id: str
    period_days: int
    generated_at: str
    summary: str
    total_knowledge_points: int
    avg_mastery: Optional[float] = None
    mastered_count: int = 0
    weak_count: int = 0
    weak_points: List[dict] = []
    items: List[dict] = []
    study: dict = {}
    recent_events: List[dict] = []
    suggestions: List[str] = []


# ---------- 模块9：合规审核 ----------
class ComplianceItem(BaseModel):
    id: Optional[int] = None
    type: str                     # 内容类型，如 题目/解析/图谱
    content: str
    status: str = "pending"       # pending / reviewed / rejected

    model_config = {"from_attributes": True}
