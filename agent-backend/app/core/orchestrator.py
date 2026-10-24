"""Orchestrator：意图路由 + 工具编排（P0 骨架）。

P0 阶段只打通链路：RAG 检索 -> LLM 解题 -> 触类旁通。
具体 LLM / 向量库 / OCR 实现见同目录各模块，P0 用占位返回，便于前后端先联调。

P2（T-C1）起增加意图三档路由：学生请求自动进 直给/讲解优先/引导 三档，
不再依赖调用方传 mode；分类失败显式降级引导档，不静默直给（见 intent.py）。
"""
from app.core import intent, llm, rag, ocr, judge_v2


async def handle_question(
    question: str,
    mode: str | None = None,
    subject: str | None = None,
    student_answer: str | None = None,
) -> dict:
    # 1) RAG 检索上下文（知识点 / 例题）
    ctx = await rag.retrieve(question, subject=subject)
    # 2) 意图三档路由（T-C1）：
    #    - 显式传 mode（含 /similar 的 similar）-> 尊重调用方，intent 不分类；
    #    - 未传 -> 规则分类 -> LLM 兜底 -> 默认引导档（不静默直给）。
    if mode:
        intent_tier, resolved_mode, intent_source = None, mode, "explicit"
    else:
        intent_tier, resolved_mode, intent_source = await intent.resolve_response_mode(
            question, ctx
        )
    # 3) LLM 解题 + 分步讲解（按档位生成）
    result = await llm.solve(question, context=ctx, mode=resolved_mode)
    # 4) 触类旁通
    similar = await llm.suggest_similar(question, context=ctx)
    out = {
        **result,
        "similar": similar,
        "knowledge_points": ctx.get("points", []),
        # 命中的标注题（步骤级归因的归属对象），事件上报与前端核对都用它
        "content_hash": judge_v2._pick_content_hash(ctx) or "",
        # T-C1：三档路由结果回传——埋点（T-C4）与 App 展示"当前为引导档/直给档"用。
        # intent 仅在实际跑了分类时有值；explicit 模式（如 similar）为 None。
        "intent": intent_tier,
        "mode": resolved_mode,
        "intent_source": intent_source,
    }
    # 5) 学生提交了自己的作答 -> judge v2 步骤级归因判分
    #    客观题/填空走查表（零 token），解答题走 LLM；都不行才 unknown。
    #    判据用 `is not None`：字段出现即表示"提交了作答"，
    #    空串是"交了白卷"，必须回 need_process 引导，而不是静默不判分。
    if student_answer is not None:
        out["judgement"] = await _judge_v2_with_fallback(question, student_answer, ctx)
    return out


async def _judge_v2_with_fallback(
    question: str, student_answer: str, ctx: dict
) -> dict:
    """judge v2 主判；低置信（unknown）时退回 v1 的对错布尔，保住原有信号。"""
    j = await judge_v2.judge(question, student_answer, context=ctx)
    # S4：被要求补过程时不要退回 v1 —— 那会白花一次 LLM 且可能把"没写过程"判成"做错"
    if j.get("need_process"):
        return j
    if j.get("attribution_source") == judge_v2.UNKNOWN:
        v1 = await llm.judge(question, student_answer, context=ctx)
        if isinstance(v1.get("correct"), bool):
            j["correct"] = v1["correct"]
            j["partial"] = bool(v1.get("partial", False))
            if not j.get("comment"):
                j["comment"] = str(v1.get("comment") or "")
    return j


async def handle_image(image_bytes: bytes) -> dict:
    # 拍照：先 OCR 抽题面，再走文本题链路
    text = await ocr.recognize(image_bytes)
    result = await handle_question(text)
    # 回传识别文本，供学情上报与前端核对（文本题链路不返回该字段）
    result["recognized_text"] = text
    return result
