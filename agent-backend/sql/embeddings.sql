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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 以下列为既有迁移增量（老库由 import_bank/sync_embeddings 的 ALTER 补齐）：
    content_hash    TEXT,                           -- 关联 course_kb 唯一键
    kp_code         VARCHAR(32),                    -- 知识点 id（数学 DAG id）
    is_demo         BOOLEAN     NOT NULL DEFAULT FALSE,  -- seed demo 假题隔离标记
    source_tag      TEXT,                           -- 来源标记（如 gk2026/manual）
    -- T-C3 题型路由：course_kb.type（单选/多选/填空/解答/实验），
    -- 见 scripts/add_qtype_to_embeddings.sql（ALTER + 回填）
    type            TEXT        NOT NULL DEFAULT ''
);

-- content_hash 唯一键：import_bank/sync_embeddings 的 ON CONFLICT (content_hash) 依赖它
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_embeddings_content_hash
    ON course_kb_embeddings (content_hash);

CREATE INDEX IF NOT EXISTS idx_kb_embeddings_subject
    ON course_kb_embeddings (subject);

-- HNSW 向量索引（余弦距离，与检索语句的 <=> 一致）
CREATE INDEX IF NOT EXISTS idx_kb_embeddings_vec
    ON course_kb_embeddings USING hnsw (embedding vector_cosine_ops);
