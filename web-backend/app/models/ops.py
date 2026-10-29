"""B 端运营实体 ORM 模型：知识点 / 错题 / 批改 / 合规。

与 course_kb 共用同一个 Base，启动时一次 create_all 全部建出。
2026-10-23：这四块原先是 API 层的内存 _STORE 占位，现改为真实持久化（PostgreSQL）。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgePoint(Base):
    """知识点（教研内容管理）。

    2026-10-24 补 understanding/memory/analogy 三列（S1 迁移已建，这里补 ORM 映射），
    供 S5 错题本展示"知识点卡"（理解/记忆抓手）。
    """

    __tablename__ = "kb_knowledge_points"

    id = Column(Integer, primary_key=True)
    subject = Column(String(32), nullable=False, index=True)
    grade = Column(String(32), index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    understanding = Column(Text)   # 理解（为什么）
    memory = Column(Text)          # 记忆抓手
    analogy = Column(Text)         # 类比
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class CourseKbStep(Base):
    """题目步骤（只读映射，表由 S1 迁移建在 106 库）。

    web-backend 侧只用于 S5 聚合：把 student_step_errors 的 (content_hash, step_no)
    翻译成人能读的"第N步 · 步骤文本 / 知识点"，并取 correction 供错题本展示。
    """

    __tablename__ = "course_kb_steps"

    content_hash = Column(String(64), primary_key=True)
    step_no = Column(Integer, primary_key=True)
    step_kind = Column(String(16))
    label = Column(Text)
    text = Column(Text)
    is_correct = Column(Boolean)
    kp_code = Column(Text)
    kp_name = Column(Text)
    kp_source = Column(Text)
    confidence = Column(Text)
    explanation = Column(Text)
    error_reason = Column(Text)
    distractor_map = Column(JSON)
    wrong_value_map = Column(JSON)
    typical_errors = Column(JSON)


class WrongQuestion(Base):
    """错题本。

    2026-10-24 补 S1 迁移新增的归因落点列 + content_hash（S5 ④ 关联步骤取 correction）。
    """

    __tablename__ = "wrong_questions"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    question = Column(Text, nullable=False)
    wrong_answer = Column(Text)
    correct_answer = Column(Text)
    knowledge_point = Column(String(128), index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    # ---- 步骤级归因落点（S1 迁移）----
    content_hash = Column(String(64), index=True)   # → course_kb.content_hash
    wrong_step_no = Column(SmallInteger)
    error_type = Column(String(16))
    evidence = Column(Text)
    attribution_source = Column(String(8))

    # ---- 学生端云同步（0004 迁移）----
    client_id = Column(String(64))       # 客户端 UUID，upsert 幂等键
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    deleted_at = Column(DateTime(timezone=True))   # 软删：同步下发 tombstone
    first_wrong_step = Column(Integer)
    comment = Column(Text)
    correction = Column(Text)
    source = Column(String(16), default="app")


class DrillCard(Base):
    """背诵卡（SM-2 间隔复习排程）。

    front/back 是卡面正反；ease/interval_days/due_at 三件是排程状态。
    客户端离线也会复习并本地改排程，联网后以 `DrillReview` 流水补传，
    服务端按**服务端权威值**重算（客户端算错不会污染库里的排程）。
    """

    __tablename__ = "drill_cards"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), index=True)
    client_id = Column(String(64))       # 同步幂等键
    kp = Column(String(128), index=True)
    front = Column(Text, nullable=False)
    back = Column(Text, nullable=False)
    source = Column(String(16), default="app")   # app / wrong（由错题生成）
    ease = Column(Float, default=2.5)
    interval_days = Column(Float, default=0.0)
    due_at = Column(DateTime(timezone=True))
    reps = Column(Integer, default=0)
    lapses = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    deleted_at = Column(DateTime(timezone=True))


class DrillReview(Base):
    """复习流水：每次评分落一条，rated_at 用客户端复习时间（离线补传也要准）。"""

    __tablename__ = "drill_reviews"

    id = Column(Integer, primary_key=True)
    student_id = Column(String(64), nullable=False, index=True)
    card_id = Column(Integer, index=True)
    client_id = Column(String(64))
    rating = Column(SmallInteger, nullable=False)   # 0 忘了 / 3 模糊 / 4 会了 / 5 太简单
    rated_at = Column(DateTime(timezone=True))
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
    """学情事件流水（来自 agent-backend 上报的 student_event）。

    2026-10-24 扩展 judge v2 步骤级归因载荷（S3 ⑤）：
    correct/partial + first_wrong_step/error_type/attribution_source/evidence，
    并补 content_hash 以便把归因挂到具体题目（供 S5 聚合）。
    """

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

    # ---- judge v2 判分结果 ----
    content_hash = Column(String(64), index=True)   # 命中的题目（归因归属）
    correct = Column(Boolean)                       # 未提交作答时为 NULL
    partial = Column(Boolean)
    first_wrong_step = Column(Integer)              # 首个出错步骤 step_no
    error_type = Column(String(16), index=True)     # taxonomy 七类 + unknown
    attribution_source = Column(String(8))          # map / llm / unknown
    evidence = Column(Text)                         # 支撑归因的学生作答片段

    # ---- T-C1/T-C3/T-C4 引导式对话埋点载荷（Hint 阶梯 / 题型路由 / 直接看解析）----
    # 旧消费者此前忽略未知字段；T-C4 起正式落地（见 scripts/extend_student_events_p2.sql）。
    # 这些列仅对 hint_shown / direct_reveal / solve 事件有意义，其余事件为 NULL。
    intent = Column(String(16), index=True)              # 意图路由结果: direct/guide/rule
    intent_source = Column(String(16))                   # rules/llm/default/explicit
    hint_level = Column(Integer)                         # Hint 阶梯当前等级 0-4
    hint_level_name = Column(String(16))                 # L0..L4 名称
    hint_rounds = Column(Integer)                        # 当前轮次
    hint_max_rounds = Column(Integer)                   # 该题型轮次上限
    hint_step = Column(Integer)                          # 分步拆解：当前步（压轴题）
    hint_step_total = Column(Integer)                   # 总步数
    hint_frustration_streak = Column(Integer)            # 连续挫败计数
    hint_finale = Column(Boolean)                        # 是否压轴题
    hint_direct_reveal = Column(Boolean)                # 是否直达 L4（主动/被动）
    question_tier = Column(String(16), index=True)      # 题型路由档: memory/medium/finale
    question_tier_name = Column(String(16))

    # ---- T-D3 降级标记 ----
    # 主解/Hint 由模板回落（LLM 配额耗尽/超时/输出违约）时为 True；
    # 埋点侧聚合"降级率"，明细用量见 llm_usage 表。
    degraded = Column(Boolean)


class StudentStepError(Base):
    """步骤级错因流水（S5 聚合燃料）。

    表由 S1 迁移脚本 p0_attribution_maps.sql 建在 106 库，这里补 ORM 模型供消费端写入。
    只记录 error_type 非空的判分结果；unknown 也记（作为人工复核队列的计数口径）。
    """

    __tablename__ = "student_step_errors"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), index=True)
    student_id = Column(String(64), nullable=False, index=True)
    content_hash = Column(String(64))
    step_no = Column(SmallInteger)
    error_type = Column(String(16), index=True)
    attribution_source = Column(String(8))
    evidence = Column(Text)
    occurred_at = Column(DateTime(timezone=True))


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


class LlmUsage(Base):
    """T-D3 LLM 用量流水（agent-backend 写入，B 端只读聚合）。

    表由 ``agent-backend/scripts/create_llm_usage.sql`` 建在 106 库，这里补 ORM
    模型供 B 端查询；启动时 create_all 对已存在的表无副作用。
    """

    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True)
    model = Column(String(64), nullable=False, default="")
    grade = Column(String(32), nullable=False, default="")
    mode = Column(String(32), nullable=False, default="")
    subject = Column(String(64))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    degraded = Column(Boolean, default=False)
    cache_hit = Column(Boolean, default=False)
    error_kind = Column(String(16))
    cost = Column(Numeric(14, 6), default=0)
    created_at = Column(DateTime(timezone=True), index=True)


# ======================================================================
# 学生 Agent『作业闭环』试卷域（MVP：高三·数学·纯选择题卷）
# 表由 migrations/0005_exam_domain.sql 创建；ORM 仅做读写映射。
# 判分唯一来源 = exam_paper_questions.answer（确定性 0-token，见 app/core/equiv.py）。
# ======================================================================
class ExamPaper(Base):
    """试卷头：年级段 / 学科 / 题型 / 发布状态。"""

    __tablename__ = "exam_papers"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), index=True)
    title = Column(String(255), nullable=False)
    paper_type = Column(String(16), nullable=False, default="pure_choice")
    grade = Column(String(32), nullable=False, default="高三")
    subject = Column(String(32), nullable=False, default="数学")
    difficulty_avg = Column(SmallInteger)
    knowledge_points = Column(JSON, default=list)
    created_by = Column(String(64))
    published = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ExamPaperQuestion(Base):
    """试卷内每题快照：题干 / 选项 / 本卷答案 / 解析。

    answer 是判分唯一来源；multi 标记选择题是否多选。
    """

    __tablename__ = "exam_paper_questions"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    content_hash = Column(String(64), index=True)
    qtype = Column(String(16), nullable=False)  # choice/blank/subjective
    multi = Column(Boolean, nullable=False, default=False)
    stem = Column(Text, nullable=False)
    options = Column(JSON)                       # {label: content}
    answer = Column(Text, nullable=False)
    analysis = Column(Text)
    difficulty = Column(SmallInteger)
    knowledge_points = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ExamAssignment(Base):
    """试卷下发：按年级段（grade）或指定学生（student）下发。"""

    __tablename__ = "exam_assignments"

    id = Column(Integer, primary_key=True)
    paper_id = Column(Integer, nullable=False, index=True)
    tenant_id = Column(String(64), index=True)
    target_type = Column(String(16), nullable=False, default="grade")  # grade/student
    target_grade = Column(String(32))
    target_student = Column(String(64))
    assigned_by = Column(String(64))
    assigned_at = Column(DateTime(timezone=True), default=_utcnow)
    due_at = Column(DateTime(timezone=True))


class ExamSubmission(Base):
    """学生整卷提交 + 自动判分结果。每学生每卷唯一一次提交。"""

    __tablename__ = "exam_submissions"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, nullable=False, index=True)
    paper_id = Column(Integer, nullable=False)
    student_id = Column(String(64), nullable=False, index=True)
    tenant_id = Column(String(64), index=True)
    client_id = Column(String(64))
    status = Column(String(16), nullable=False, default="graded")  # graded/manual_pending
    score = Column(Numeric(6, 2))            # 正确题数
    correct_count = Column(Integer)
    total_count = Column(Integer)
    submitted_at = Column(DateTime(timezone=True), default=_utcnow)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class ExamSubmissionItem(Base):
    """逐题判分结果。correct=全对；partial=多选漏选但所选皆正确（MVP 标记不计分）。"""

    __tablename__ = "exam_submission_items"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, nullable=False, index=True)
    paper_question_id = Column(Integer, nullable=False)
    seq = Column(Integer, nullable=False)
    qtype = Column(String(16), nullable=False)
    student_answer = Column(Text)
    correct = Column(Boolean)
    partial = Column(Boolean, default=False)
    status = Column(String(16), nullable=False, default="auto")  # auto/manual_pending
    created_at = Column(DateTime(timezone=True), default=_utcnow)
