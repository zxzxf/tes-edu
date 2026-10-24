-- ============================================================================
-- T-D1 请求级缓存持久化兜底表：llm_cache
-- 对齐《P2 引导式对话与横向收尾任务计划书》T-D1：
--   同题/近似题命中缓存，杜绝重复烧 LLM。Redis（137 edu-redis）为主缓存（TTL 7 天），
--   本表为 PG 持久化兜底（防 Redis 清空），并承载"近似命中"所需的向量相似度检索。
--
-- 维度的 embedding 列宽需与 EMBED_DIM 一致（当前部署 1024，qwen3.7-text-embedding）。
-- 若后续 EMBED_DIM 变更，需同步调整 vector(1024) 的宽度。
--
-- 依赖：pgvector 扩展（course_kb_embeddings 已用过，库里应已存在；保险起见 IF NOT EXISTS）。
-- 全部幂等：可重复执行。
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS llm_cache (
    key          TEXT PRIMARY KEY,
    question     TEXT,
    mode         VARCHAR(16),
    subject      VARCHAR(64),
    hint_level   INTEGER,
    result_json  JSONB NOT NULL,
    embedding    vector(1024),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ
);

-- 近似命中：向量余弦相似度检索（pgvector 的 <=> 是 L2 距离，1 - 距离 = 余弦）
CREATE INDEX IF NOT EXISTS idx_llm_cache_embedding
    ON llm_cache USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 兜底清理/按学科检索辅助
CREATE INDEX IF NOT EXISTS idx_llm_cache_subject_mode
    ON llm_cache (subject, mode);

-- 近似命中命名空间：必须与 (subject, mode, hint_level) 三元组严格对齐，
-- 否则会跨 Hint 级 / 跨档位复用 LLM 输出（串味）。
CREATE INDEX IF NOT EXISTS idx_llm_cache_namespace
    ON llm_cache (subject, mode, hint_level);
