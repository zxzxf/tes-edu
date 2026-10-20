"""模块2：学情看板与薄弱点诊断。

聚合 student_logs + 向量检索，输出薄弱知识点与掌握度趋势。
"""
from fastapi import APIRouter, Depends, Query

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import AnalyticsSummary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=AnalyticsSummary)
def summary(
    student_id: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
):
    # 占位：真实场景从 student_logs + pgvector 聚合薄弱点
    return AnalyticsSummary(
        student_id=student_id,
        weak_points=["二次函数", "被动语态", "几何证明", "文言文虚词"],
        mastery=0.72,
        trend=[0.55, 0.6, 0.63, 0.68, 0.72, 0.74],
    )
