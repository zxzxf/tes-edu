-- ============================================================================
-- T-D3 LLM 用量表：llm_usage
-- 对齐《P2 引导式对话与横向收尾任务计划书》T-D3：
--   每次远程 LLM 调用落一行（model / token / latency / degraded / cost），
--   缓存命中也记一行（token 与 cost 为 0）用于统计"缓存省了多少"；
--   日预算告警（web /api/admin/llm-usage）按本表聚合当日成本。
--
-- 全部幂等：可重复执行。
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm_usage (
    id                BIGSERIAL PRIMARY KEY,
    model             VARCHAR(64)  NOT NULL DEFAULT '',
    grade             VARCHAR(32)  NOT NULL DEFAULT '',  -- simple/hard/single/cache/hard_forced_simple
    mode              VARCHAR(32)  NOT NULL DEFAULT '',  -- explain/quick/guide/similar/judge/direct
    subject           VARCHAR(64),
    prompt_tokens     INTEGER      NOT NULL DEFAULT 0,
    completion_tokens INTEGER      NOT NULL DEFAULT 0,
    latency_ms        INTEGER      NOT NULL DEFAULT 0,
    degraded          BOOLEAN      NOT NULL DEFAULT FALSE,  -- 是否走了模板降级
    cache_hit         BOOLEAN      NOT NULL DEFAULT FALSE,  -- 是否缓存命中（未真调 LLM）
    error_kind        VARCHAR(16),                          -- quota/timeout/other，成功为 NULL
    cost              NUMERIC(14, 6) NOT NULL DEFAULT 0,   -- 估算成本（元）
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 日预算聚合（按天扫表）与按模型聚合
CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at ON llm_usage (created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model_day ON llm_usage (model, created_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_degraded ON llm_usage (degraded, created_at);
