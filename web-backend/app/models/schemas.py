"""Pydantic 数据模型（请求/响应）。

P0/P2 阶段先定义契约，真实持久化在接入 PostgreSQL 后补齐。
"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------- 认证 ----------
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    # T-WB1（2026-10-28）：App 端错题/背诵同步要用真实流水号作 student_id。
    # 兼容旧客户端设为可选——缺字段时 App 回落到占位学号，不会因解析失败崩。
    user_id: str | None = None
    tenant_id: str | None = None


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
    # S5 ④ 步骤级归因落点（录入时可选）
    content_hash: Optional[str] = None
    wrong_step_no: Optional[int] = None
    error_type: Optional[str] = None
    evidence: Optional[str] = None
    attribution_source: Optional[str] = None

    model_config = {"from_attributes": True}


class WrongQuestionOut(WrongQuestion):
    """错题本列表返回：原字段 + 步骤级增强（错因中文名 / 该步正确做法 / 知识点卡）。"""

    created_at: Optional[str] = None
    error_type_label: Optional[str] = None
    step_text: Optional[str] = None
    step_label: Optional[str] = None
    kp_name: Optional[str] = None
    correction: Optional[str] = None      # 可直接展示的"这一步应该怎么做"
    correction_matched: bool = False      # True=针对该错因；False=该步的通用参考做法
    explanation: Optional[str] = None     # 该步详解
    understanding: Optional[str] = None   # 知识点卡：理解
    memory: Optional[str] = None          # 知识点卡：记忆抓手
    advice: Optional[str] = None          # 该类错因的一句话建议


# ---------- 作业闭环：试卷域（MVP 高三·数学·纯选择题卷） ----------
class OptionItem(BaseModel):
    label: str
    content: str


class ExamPaperQuestionOut(BaseModel):
    seq: int
    qtype: str
    multi: bool = False
    stem: str
    options: List[dict] = []
    answer: Optional[str] = None
    analysis: Optional[str] = None
    difficulty: Optional[int] = None
    knowledge_points: List[str] = []
    # M2-4：可接受答案值集合（标答预展开 + 教师批改时确认追加），教师校对用
    answer_values: List[str] = []


class ExamPaperCreate(BaseModel):
    """规则组卷请求：按年级/学科/题型/题量/难度从 course_kb 抽题。

    ``paper_type`` 三值（M1-3）：``pure_choice`` 纯选择题卷 / ``pure_blank`` 纯填空题卷 /
    ``mixed`` 混合卷（含解答题，发布时标「需人工 N 道」）。
    """
    title: Optional[str] = None
    grade: str = "高三"
    subject: str = "数学"
    paper_type: Literal["pure_choice", "pure_blank", "mixed"] = "pure_choice"
    count: int = Field(default=20, ge=1, le=100)      # 抽题数量
    knowledge_points: List[str] = []                  # 可选：限定知识点
    difficulty_min: Optional[int] = None
    difficulty_max: Optional[int] = None


class ExamPaperOut(BaseModel):
    id: int
    title: str
    paper_type: str
    grade: str
    subject: str
    published: bool
    question_count: int = 0
    auto_gradeable_count: int = 0
    manual_required_count: int = 0
    questions: List[ExamPaperQuestionOut] = []


# ---- 发布校验（M1-3）----
class PaperViolationOut(BaseModel):
    seq: int                     # 0 = 整卷级问题
    qtype: str = ""
    code: str
    message: str


class PaperValidationOut(BaseModel):
    """发布前校验结果。``ok=false`` 时 ``violations`` 列出全部拦截原因。"""

    ok: bool
    paper_id: int
    paper_type: str
    question_count: int = 0
    qtype_counts: Dict[str, int] = {}
    auto_gradeable_count: int = 0
    manual_required_count: int = 0
    manual_breakdown: Dict[str, int] = {}
    violations: List[PaperViolationOut] = []
    note: str = ""


class AssignmentCreate(BaseModel):
    target_type: str = "grade"               # grade / student
    target_grade: Optional[str] = "高三"
    target_student: Optional[str] = None
    due_at: Optional[str] = None             # ISO 字符串


class AssignmentOut(BaseModel):
    assignment_id: int
    paper_id: int
    title: str
    paper_type: str
    grade: str
    subject: str
    target_type: str
    target_grade: Optional[str] = None
    target_student: Optional[str] = None
    question_count: int = 0
    due_at: Optional[str] = None


# ---- 学生端契约 ----
class StudentExamListItem(BaseModel):
    assignment_id: int
    paper_id: int
    title: str
    paper_type: str
    grade: str
    subject: str
    question_count: int = 0
    due_at: Optional[str] = None
    submitted: bool = False


class StudentQuestionView(BaseModel):
    seq: int
    qtype: str
    multi: bool = False
    stem: str
    options: List[dict] = []
    difficulty: Optional[int] = None
    knowledge_points: List[str] = []
    # 填空题空数（多空题需逐空输入；由标答结构推出，不泄露答案内容）
    blank_count: int = 0


class SubmitItem(BaseModel):
    seq: int
    student_answer: Optional[str] = None
    # 多空题逐空答案（按空序号）。有则优先于 student_answer 单串切分；
    # 保留 student_answer 是为了向后兼容旧版 App 的单串提交。
    blanks: Optional[List[str]] = None


class SubmitExamRequest(BaseModel):
    client_id: Optional[str] = None
    answers: List[SubmitItem]


class ExamResultItem(BaseModel):
    seq: int
    qtype: str
    multi: bool = False
    student_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    correct: bool = False
    partial: bool = False
    # auto=机器判；manual_pending=待人工；manual_reviewed=老师已判定
    status: str = "auto"
    # 判分依据说明：转人工时写清原因（符号/表达式类答案、多空空数不符），
    # 让学生与教师都知道「不是判错，是判不了」
    reason: Optional[str] = None
    analysis: Optional[str] = None
    # 老师判定时写的评语（manual_reviewed 才有），学生端据此知道为什么算错
    teacher_comment: Optional[str] = None


class ExamResult(BaseModel):
    submission_id: int
    assignment_id: int
    paper_id: int
    title: str
    score: float = 0.0                       # 正确题数
    correct_count: int = 0
    total_count: int = 0
    # 混合卷（含解答/填空）下 total 包含人工题，前端按这两个数渲染「已判 x/y · z 题待人工」
    auto_gradeable_count: int = 0
    manual_pending_count: int = 0
    # 其中已由老师复核判定的题数（含改判），与人工判定结果一并回写
    manual_reviewed_count: int = 0
    status: str = "graded"
    submitted_at: Optional[str] = None
    items: List[ExamResultItem] = []


# ---------- 模块5：教师批改（人工评定工单，M2-5/M2-6） ----------
class Submission(BaseModel):
    id: Optional[int] = None
    student_id: str
    answer: str
    score: Optional[float] = None
    comment: Optional[str] = None

    model_config = {"from_attributes": True}


# 工单状态 / 判定枚举（字符串常量集中在此，避免前后端各写一套拼错的字面量）
TICKET_PENDING = "pending"
TICKET_CLAIMED = "claimed"
TICKET_REVIEWED = "reviewed"
VERDICT_CORRECT = "correct"
VERDICT_PARTIAL = "partial"
VERDICT_WRONG = "wrong"


class GradingTicketOut(BaseModel):
    """工单待办条目（列表页用，不含题干）。"""

    ticket_id: int
    status: str = TICKET_PENDING
    qtype: str
    seq: int
    student_id: str
    assignment_id: int
    paper_id: int
    paper_title: str = ""
    student_answer: Optional[str] = None
    reason: Optional[str] = None          # 为什么转人工（机器判不了的具体原因）
    verdict: Optional[str] = None
    comment: Optional[str] = None
    assignee: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: Optional[str] = None


class GradingRevisionOut(BaseModel):
    """改判留痕条目。"""

    action: str
    actor_id: Optional[str] = None
    actor_role: Optional[str] = None
    before_correct: Optional[bool] = None
    after_correct: Optional[bool] = None
    before_partial: Optional[bool] = None
    after_partial: Optional[bool] = None
    before_status: Optional[str] = None
    after_status: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None


class GradingTicketDetail(GradingTicketOut):
    """工单详情：题干/选项/标答/解析 + 已确认答案值 + 留痕，教师据此判定。"""

    stem: str = ""
    options: List[dict] = []
    correct_answer: Optional[str] = None
    analysis: Optional[str] = None
    knowledge_points: List[str] = []
    answer_values: List[str] = []
    revisions: List[GradingRevisionOut] = []


class GradingTicketPage(BaseModel):
    """待办分页 + 各状态计数（前端顶部 tab 直接取用）。"""

    total: int = 0
    counts: Dict[str, int] = {}           # {pending, claimed, reviewed}
    page: int = 1
    page_size: int = 20
    items: List[GradingTicketOut] = []


class GradingJudgeRequest(BaseModel):
    """教师判定请求。

    ``accept_answer``：确认「学生这种写法也算对」—— 把学生答案追加进该题的
    ``answer_values``，**后续同一道题直接自动判对**，不再反复转人工（M2-4 闭环）。
    """

    verdict: Literal["correct", "partial", "wrong"]
    comment: Optional[str] = None
    accept_answer: bool = False


class GradingClaimRequest(BaseModel):
    comment: Optional[str] = None


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
    # S5 步骤级薄弱点：{total_errors, error_types:[{error_type,label,count,ratio,advice}],
    #                  top_steps:[{content_hash,step_no,step_label,count,main_error_type,...}],
    #                  headline, advice}
    step_weakness: dict = {}
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
