"""模块3：错题本管理。

教师/家长查看学生错题；录入错题（可由 Agent 后端自动同步）。
"""
from typing import List

from fastapi import APIRouter, Depends, Query

from app.core.rbac import Role
from app.core.security import require_role
from app.models.schemas import WrongQuestion

router = APIRouter(prefix="/wrong-questions", tags=["wrong-questions"])

# 占位内存存储；预置 student_001 的示例错题用于演示
_STORE: List[WrongQuestion] = [
    WrongQuestion(id=1, student_id="student_001", question="求 y=2x²-4x+1 的最小值", wrong_answer="-1", correct_answer="-1（当 x=1 时）", knowledge_point="二次函数"),
    WrongQuestion(id=2, student_id="student_001", question="翻译：这本书被译成多种语言", wrong_answer="This book translated many languages", correct_answer="This book has been translated into many languages", knowledge_point="被动语态"),
    WrongQuestion(id=3, student_id="student_001", question="Rt△ABC 中 ∠C=90°，a=3,b=4，求 c", wrong_answer="6", correct_answer="5", knowledge_point="勾股定理"),
    WrongQuestion(id=4, student_id="student_001", question="用英语写含 who 的定语从句描述你的老师", wrong_answer="My teacher who is tall.", correct_answer="My teacher is a person who is tall.", knowledge_point="定语从句"),
    WrongQuestion(id=5, student_id="student_001", question="U=6V,R=3Ω，求电流 I", wrong_answer="18A", correct_answer="2A", knowledge_point="欧姆定律"),
    WrongQuestion(id=6, student_id="student_002", question="相似三角形的判定方法有几条", wrong_answer="2", correct_answer="3", knowledge_point="相似三角形判定"),
]


@router.get("", response_model=List[WrongQuestion])
def list_wrong(
    student_id: str = Query(...),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
):
    return [w for w in _STORE if w.student_id == student_id]


@router.post("", response_model=WrongQuestion)
def add_wrong(w: WrongQuestion, user=Depends(require_role(Role.ADMIN, Role.TEACHER))):
    w.id = len(_STORE) + 1
    _STORE.append(w)
    return w
