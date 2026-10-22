"""Embedding 接入层（P2 启用）。

当前未接入，course_kb.embedding 保持 NULL；/similar 语义检索在 P2 实装。
函数签名预留，便于后续直接替换实现。
"""
from typing import List


async def embed_texts(texts: List[str]) -> List[List[float]]:
    # TODO(P2)：调 LLM 白名单内的 embedding 模型（settings.embed_model），
    # 批量生成向量并写回 course_kb.embedding，再建 pgvector 索引 + /similar 语义检索。
    raise NotImplementedError("embedding 尚未接入（计划书 P2）")
