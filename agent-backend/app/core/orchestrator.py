"""Orchestrator：意图路由 + 工具编排（P0 骨架）。

P0 阶段只打通链路：RAG 检索 -> LLM 解题 -> 触类旁通。
具体 LLM / 向量库 / OCR 实现见同目录各模块，P0 用占位返回，便于前后端先联调。
"""
from app.core import llm, rag, ocr


async def handle_question(
    question: str,
    mode: str = "explain",
    subject: str | None = None,
    student_answer: str | None = None,
) -> dict:
    # 1) RAG 检索上下文（知识点 / 例题）
    ctx = await rag.retrieve(question, subject=subject)
    # 2) LLM 解题 + 分步讲解
    result = await llm.solve(question, context=ctx, mode=mode)
    # 3) 触类旁通
    similar = await llm.suggest_similar(question, context=ctx)
    out = {
        **result,
        "similar": similar,
        "knowledge_points": ctx.get("points", []),
    }
    # 4) 学生提交了自己的作答 -> 顺带判分（产出 correct 信号，供掌握度计算）
    if student_answer:
        out["judgement"] = await llm.judge(question, student_answer, context=ctx)
    return out


async def handle_image(image_bytes: bytes) -> dict:
    # 拍照：先 OCR 抽题面，再走文本题链路
    text = await ocr.recognize(image_bytes)
    result = await handle_question(text)
    # 回传识别文本，供学情上报与前端核对（文本题链路不返回该字段）
    result["recognized_text"] = text
    return result
