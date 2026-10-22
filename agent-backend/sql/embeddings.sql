-- 向量库表结构（106 的 pgvector/pg16）
-- 维度需与 embedding 模型一致（EMBED_DIM，默认 1024）；换模型需重建该列与索引。

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS course_kb_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    subject         TEXT        NOT NULL DEFAULT 'math',
    knowledge_point TEXT        NOT NULL DEFAULT '',
    difficulty      TEXT        NOT NULL DEFAULT '',
    question        TEXT        NOT NULL,
    answer          TEXT        NOT NULL DEFAULT '',
    content         TEXT        NOT NULL,            -- 送检索的文本（题干 + 解析）
    embedding       VECTOR(1024),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_embeddings_subject
    ON course_kb_embeddings (subject);

-- HNSW 向量索引（余弦距离，与检索语句的 <=> 一致）
CREATE INDEX IF NOT EXISTS idx_kb_embeddings_vec
    ON course_kb_embeddings USING hnsw (embedding vector_cosine_ops);
