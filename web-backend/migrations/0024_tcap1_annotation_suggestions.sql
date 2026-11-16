-- 0024_tcap1_annotation_suggestions.sql
-- T-CAP1 标注自动化管线（L3）：LLM 预标建议 → 待校队列 → 人工确认写回。
-- 2026-11-16
--
-- 背景：线上 course_kb 257 题中，知识点/题型已全标，但 difficulty 大量仍是默认值 3、
--       year 数学 46 空、paper_type 数学 34 空。T-CAP1 聚焦补全这三类字段。
--
-- 红线（M0-2 审核隔离）：LLM 建议**不直接写 course_kb**（该表含已发布题），
--       一律先进本表 status='pending'，人工 approve 后才由 apply 服务写回，
--       reject 的不写回。非数值/拿不准的题，reason 里标注，走人工复核。
--
-- 幂等：唯一键 (content_hash, field_name) 保证同一题同一字段只生成一次建议，
--       续跑时跳过已有建议行，不重复烧 token；prompt 改版用 prompt_version 区分。
-- 可重复执行：CREATE TABLE IF NOT EXISTS。
--
-- ⚠️ 须在 web-backend 新镜像发版前执行；线上库为 PostgreSQL。

BEGIN;

CREATE TABLE IF NOT EXISTS course_kb_annotation_suggestions (
    id              BIGSERIAL PRIMARY KEY,
    content_hash    VARCHAR(64)   NOT NULL,
    field_name      VARCHAR(24)   NOT NULL,   -- difficulty / year / paper_type
    suggested_value TEXT          NOT NULL,   -- 建议值（文本化，apply 时按字段转型）
    reason          TEXT,                     -- LLM 给出的简短依据 / 存疑说明（供人工审核）
    source_model    VARCHAR(64),              -- 生成该建议的模型名
    prompt_version  INTEGER       NOT NULL DEFAULT 1,
    status          VARCHAR(16)   NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    reviewed_by     VARCHAR(64),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT uq_kb_anno_hash_field UNIQUE (content_hash, field_name)
);

CREATE INDEX IF NOT EXISTS idx_kb_anno_status ON course_kb_annotation_suggestions (status);
CREATE INDEX IF NOT EXISTS idx_kb_anno_hash   ON course_kb_annotation_suggestions (content_hash);

COMMIT;
