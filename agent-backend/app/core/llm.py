"""LLM 解答引擎接入骨架（P0 占位，后续接 通义千问 / DeepSeek / 私有化）。"""


async def solve(question: str, context: dict | None = None, mode: str = "explain") -> dict:
    # TODO: 调用 LLM，拼装 system prompt（含 RAG context 防幻觉）+ 分步推导
    return {
        "answer": f"[P0骨架] 针对「{question}」的解答将在此生成。",
        "steps": ["(1) 审题", "(2) 知识点定位", "(3) 分步推导", "(4) 总结易错点"],
    }


async def suggest_similar(question: str, context: dict | None = None) -> list[str]:
    # TODO: 基于知识图谱 / 向量召回生成变式题
    return ["[P0骨架] 变式题 1", "[P0骨架] 变式题 2", "[P0骨架] 变式题 3"]
