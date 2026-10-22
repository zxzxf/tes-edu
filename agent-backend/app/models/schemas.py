from typing import Optional

from pydantic import BaseModel


class SolveRequest(BaseModel):
    question: str
    grade: Optional[str] = None
    subject: Optional[str] = None
    mode: Optional[str] = "explain"  # explain | similar
    # 学生自己的作答；填了才会触发判分（产出 correct 信号，用于掌握度）
    student_answer: Optional[str] = None


class SimilarRequest(BaseModel):
    question: str
    subject: Optional[str] = None
    count: int = 3


class SolveResponse(BaseModel):
    answer: str
    steps: list[str] = []
    similar: list[str] = []
    knowledge_points: list[str] = []
    # 仅当提交了 student_answer 才有：{"correct": bool, "partial": bool, "comment": str}
    judgement: Optional[dict] = None
