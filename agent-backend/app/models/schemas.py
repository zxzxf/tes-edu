from typing import Optional

from pydantic import BaseModel


class SolveRequest(BaseModel):
    question: str
    grade: Optional[str] = None
    subject: Optional[str] = None
    # T-C1：不传（None）则按学生意图自动三档路由（直给/讲解优先/引导）；
    # 显式传值则尊重调用方（explain | guide | quick | similar）。
    # T-C3：mode=direct 是一等功能"直接看解析"的服务端入口——
    # 直达 L4 完整解析 + 复盘三问（等价 direct_reveal=true 的阶梯轮），
    # 点击行为入埋点（event_type=direct_reveal）且不作为违规（R6）。
    mode: Optional[str] = None
    # 学生自己的作答；填了才会触发判分（产出 correct 信号，用于掌握度）
    student_answer: Optional[str] = None
    # T-C2：Hint 阶梯对话开关。true 时按 (学生, 题) 维护 L0–L4 阶梯状态，
    # 每轮返回当前等级 Hint（socratic.py）；阶梯只存在于引导档
    hint: bool = False
    # T-C2：对话轮中学生的回应（挫败信号"我不懂/直接说吧"检测 + LLM 对话历史用）
    student_reply: Optional[str] = None
    # T-C2：学生主动点"直接看解析" -> 直达 L4；
    # T-C3 起等价入口为 mode=direct（App 常驻按钮走后者）
    direct_reveal: bool = False


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
    # T-C3 题型路由回传（qtype.route_question_tier）：
    # question_tier=memory/medium/finale；question_tier_name=记忆型/中等/压轴；
    # question_type=course_kb.type 原值（单选/多选/填空/解答/实验）；
    # hint_max_rounds=该题引导轮次上限（记忆 1 / 中等 2 / 压轴 4）。
    # App 据此决定是否展示"直接看解析"常驻按钮与阶梯入口；埋点按题型分层。
    question_tier: Optional[str] = None
    question_tier_name: Optional[str] = None
    question_type: Optional[str] = None
    hint_max_rounds: Optional[int] = None
    # T-C2 Hint 阶梯回传（仅 hint=true 的轮次有值）：
    # hint_level=当前等级 0–4；hint_level_name=等级名（点考点/给方向/给关键公式/
    # 给该步推导/完整解析+复盘）；hint_frustration_streak=连续挫败次数；
    # hint_finale=是否压轴题（封顶 L3）；hint_direct_reveal=本轮是否直达 L4；
    # hint_rounds=本题已进行的阶梯轮次（对照 hint_max_rounds 展示进度）。
    # 供 App 展示等级进度与埋点（T-C4）聚合推进率/挫败率。
    hint_level: Optional[int] = None
    hint_level_name: Optional[str] = None
    hint_frustration_streak: Optional[int] = None
    hint_finale: Optional[bool] = None
    hint_direct_reveal: Optional[bool] = None
    hint_rounds: Optional[int] = None
    # T-C3 分步拆解回传（仅压轴题 hint 轮有值）：hint_step=当前步骤号；
    # hint_step_total=总步骤数；hint_step_text=当前步骤文本（App 展示"第 N/M 步"）
    hint_step: Optional[int] = None
    hint_step_total: Optional[int] = None
    hint_step_text: Optional[str] = None
    # 仅当提交了 student_answer 才有（judge v2 步骤级归因）：
    # {"correct","partial","comment","first_wrong_step","error_type","evidence",
    #  "correction","attribution_source"}
    judgement: Optional[dict] = None
