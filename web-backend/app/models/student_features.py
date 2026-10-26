"""学生端能力扩展 ORM 模型（W3：T-F / T-S / T-A）。

五张表，与 course_kb 共用同一个 Base，随 Base.metadata.create_all 一并建出：
- feature_switch            功能开关定义（#2 总闸，6 枚默认开）
- feature_switch_override   功能开关按范围覆盖（global/region/tenant/class）
- anti_fatigue_config       学习防疲劳配置（#3）
- student_project           学生自建学习项目（#1）
- student_project_task      项目任务清单（#1）

范围层级解析优先级（越具体越优先）：
    class > tenant > region > global > default_on
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# #2 功能开关（总闸）
# ---------------------------------------------------------------------------
class FeatureSwitch(Base):
    """功能开关定义表。每枚学生端可显隐功能注册一枚 feature_key。"""

    __tablename__ = "feature_switch"

    feature_key = Column(String(64), primary_key=True)  # project / anti_fatigue / ...
    feature_name = Column(String(128), nullable=False)
    platform = Column(String(16), nullable=False, default="both")  # app / web / both
    default_on = Column(Boolean, nullable=False, default=True)
    description = Column(Text)


class FeatureSwitchOverride(Base):
    """功能开关按范围覆盖表。scope_type 越具体优先级越高。"""

    __tablename__ = "feature_switch_override"
    __table_args__ = (
        UniqueConstraint(
            "feature_key", "scope_type", "scope_id", name="uq_feature_override"
        ),
    )

    feature_key = Column(String(64), primary_key=True)
    scope_type = Column(String(16), primary_key=True)  # global / region / tenant / class
    scope_id = Column(String(64), primary_key=True, default="")  # global 时为空串
    enabled = Column(Boolean, nullable=False)
    updated_by = Column(String(64))
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# ---------------------------------------------------------------------------
# #3 学习防疲劳配置
# ---------------------------------------------------------------------------
class AntiFatigueConfig(Base):
    """防疲劳配置。按范围（global/region/tenant/class）一套配置。"""

    __tablename__ = "anti_fatigue_config"

    scope_type = Column(String(16), primary_key=True)  # global / region / tenant / class
    scope_id = Column(String(64), primary_key=True, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    interval_min = Column(Integer, nullable=False, default=25)  # 连续学习提醒间隔(分钟)
    break_min = Column(Integer, nullable=False, default=5)  # 建议休息(分钟)
    mode = Column(String(16), nullable=False, default="remind")  # remind 仅提醒 / enforce 强制
    message = Column(
        Text, nullable=False, default="该休息一下啦，远眺放松眼睛，保护视力~"
    )


# ---------------------------------------------------------------------------
# #1 学生自建学习项目
# ---------------------------------------------------------------------------
class StudentProject(Base):
    """学生自建学习工作区（如"攻克电磁感应"），纯自驱。"""

    __tablename__ = "student_project"

    project_id = Column(String(64), primary_key=True)
    student_id = Column(String(64), nullable=False)  # App/网页端学生用户
    tenant_id = Column(String(64), nullable=False)  # 学校隔离
    title = Column(String(255), nullable=False)
    description = Column(Text)
    goal = Column(Text)
    kp_code = Column(String(64))  # 可选：关联知识点(DAG code)
    status = Column(SmallInteger, default=1)  # 1 进行中 / 0 归档
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StudentProjectTask(Base):
    """项目任务清单。进度由 done 聚合得出。"""

    __tablename__ = "student_project_task"

    task_id = Column(String(64), primary_key=True)
    project_id = Column(
        String(64), ForeignKey("student_project.project_id", ondelete="CASCADE"), nullable=False
    )
    title = Column(String(255), nullable=False)
    done = Column(Boolean, nullable=False, default=False)
    due_at = Column(DateTime(timezone=True))
    sort = Column(Integer, default=0)
