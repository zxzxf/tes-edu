from typing import Optional

from pydantic import BaseModel


class SolveRequest(BaseModel):
    question: str
    grade: Optional[str] = None
    subject: Optional[str] = None
    # T-C1：不传（None）则按学生意图自动三档路由（直给/讲解优先/引导）；
    # 显式传值则尊重调用方（explain | guide | quick | similar）
    mode: Optional[str] = None
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
    # 命中的标注题 hash：步骤级归因的归属对象（前端可据此回查该题）
    content_hash: Optional[str] = None
    # T-C1 意图三档路由回传：intent=direct/explain/guide（explicit 模式为 None），
    # mode=实际使用的生成档位，intent_source=rules/llm/default/explicit。
    # 供埋点（T-C4）与 App 展示"当前为引导档/直给档"。
    intent: Optional[str] = None
    mode: Optional[str] = None
    intent_source: Optional[str] = None
    # 仅当提交了 student_answer 才有（judge v2 步骤级归因）：
    # {"correct","partial","comment","first_wrong_step","error_type","evidence",
    #  "correction","attribution_source"}
    judgement: Optional[dict] = None
