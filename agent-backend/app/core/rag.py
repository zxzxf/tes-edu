"""RAG 检索骨架（P0 占位，后续接 pgvector 向量检索，见方案 embeddings 表）。"""


async def retrieve(query: str, subject: str | None = None) -> dict:
    # TODO: 向量化 query -> 查 embeddings 表 -> 召回知识点/例题 chunk
    return {"chunks": [], "points": []}
