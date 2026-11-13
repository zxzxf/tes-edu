-- M5-7 课程源定时自主发现：数据模型扩展
-- 2026-11-13
--
-- 仅在 PostgreSQL（生产 137）执行；本地 pytest 由 Base.metadata.create_all 自动建列，
-- 不依赖本文件。PostgreSQL 支持 ADD COLUMN IF NOT EXISTS，可重复执行。

ALTER TABLE course_source ADD COLUMN IF NOT EXISTS auto_discovered BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE course_source ADD COLUMN IF NOT EXISTS review_status VARCHAR(16);

ALTER TABLE crawl_job ADD COLUMN IF NOT EXISTS job_kind VARCHAR(16) NOT NULL DEFAULT 'harvest';
ALTER TABLE crawl_job ADD COLUMN IF NOT EXISTS query TEXT;

ALTER TABLE crawl_run ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'manual';
