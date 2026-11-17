-- M5-7 补丁：自动发现草稿任务记录来源发现任务，审核通过后继承 cron 并转 P1 定时出卷。
-- 之前草稿任务 schedule=NULL + paused，即使审核通过也永远不会被调度器捞到，
-- 「自动发现 → 审核 → 自动采集出卷」链路在最后一步断开。
ALTER TABLE crawl_job ADD COLUMN IF NOT EXISTS discovery_job_id INTEGER;
CREATE INDEX IF NOT EXISTS ix_crawl_job_discovery_job ON crawl_job (discovery_job_id);
