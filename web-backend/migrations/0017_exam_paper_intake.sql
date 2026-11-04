-- M4-3/M4-4 采集卷入库两条路径 + LLM 草稿题的来源追溯（2026-11-03）
--
-- P1 整卷入库 / P2 拆题入库都写一行 intake：回退时按 run_id 精确撤销，
-- 不按 job_id 批量删除（同一 job 会跑很多次，按 job 会误删历史好数据）。
--
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS exam_paper_intake (
    id           BIGSERIAL PRIMARY KEY,
    paper_id     BIGINT,
    tenant_id    VARCHAR(64),
    job_id       BIGINT,
    run_id       BIGINT,
    source       VARCHAR(32) NOT NULL DEFAULT 'auto_crawl',
    question_ids JSON,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_intake_tenant ON exam_paper_intake (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS ix_intake_paper  ON exam_paper_intake (paper_id);
COMMENT ON TABLE exam_paper_intake IS 'M4-4 采集入库追溯：按 run 一键回退，P1 记卷、P2 记题';

-- M4-4：P1 整卷入库时记录本次建出的卷，指标/日志页可直接回看
ALTER TABLE crawl_run ADD COLUMN IF NOT EXISTS paper_id BIGINT;
CREATE INDEX IF NOT EXISTS ix_crawl_run_paper ON crawl_run (paper_id);
COMMENT ON COLUMN crawl_run.paper_id IS 'M4-4 P1 整卷入库产出卷；P2 为空';
