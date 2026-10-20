"""模块6：家长端周报。

按学生 + 周次生成学习周报（完成量、薄弱点、建议）。
"""
from fastapi import APIRouter, Depends, Query

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import WeeklyReport

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/weekly", response_model=WeeklyReport)
def weekly(
    student_id: str = Query(...),
    week: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
):
    return WeeklyReport(
        student_id=student_id,
        week=week,
        summary="本周完成 12 题，正确率 78%；建议加强几何证明与文言文虚词的练习。",
        weak_points=["几何证明", "文言文虚词", "二次函数"],
    )
