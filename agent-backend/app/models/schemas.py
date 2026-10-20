from typing import Optional

from pydantic import BaseModel


class SolveRequest(BaseModel):
    question: str
    grade: Optional[str] = None
    subject: Optional[str] = None
    mode: Optional[str] = "explain"  # explain | similar


class SimilarRequest(BaseModel):
    question: str
    subject: Optional[str] = None
    count: int = 3


class SolveResponse(BaseModel):
    answer: str
    steps: list[str] = []
    similar: list[str] = []
    knowledge_points: list[str] = []
