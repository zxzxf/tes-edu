"""模型包：集中 import，确保 Base.metadata 能收集到所有表（供 create_all 建表）。"""
from app.models import course_kb  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.models import student_features  # noqa: F401
from app.models import ops  # noqa: F401
