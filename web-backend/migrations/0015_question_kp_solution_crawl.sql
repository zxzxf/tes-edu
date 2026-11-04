-- M3-1/3/6/8 + M5-1/3 数据模型（2026-11-03）
--
-- 本次新增 6 张表：
--   * question_kp    题↔知识点绑定（M3-3；kp_code 写侧唯一入口仍是 resolve_kp_codes）
--   * exam_solutions 按卷题缓存的解析（M3-1；幂等键 (paper_id, paper_question_id)）
--   * course_source  课程源（M5-1；文件/URL/题库包，可启停）
--   * crawl_job      采集任务（M3-8；cron + budget_limit）
--   * crawl_run      采集运行日志（M5-6；每次执行一条，含去重/失败计数）
--   * kp_node        候选知识点（M5-3；新节点默认 draft，审核后进 kb_knowledge_points）
--
-- 幂等：CREATE TABLE/INDEX IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS question_kp (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    VARCHAR(64),
    question_id  BIGINT,
    content_hash VARCHAR(64),
    kp_code      VARCHAR(32) NOT NULL,
    weight       NUMERIC(4,2) NOT NULL DEFAULT 1.0,
    source       VARCHAR(16) NOT NULL DEFAULT 'manual',  -- manual/import/crawl/llm
    confidence   NUMERIC(4,3),
    reviewed_by  VARCHAR(64),
    created_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_question_kp UNIQUE (question_id, kp_code)
);
CREATE INDEX IF NOT EXISTS ix_question_kp_kp     ON question_kp (kp_code);
CREATE INDEX IF NOT EXISTS ix_question_kp_hash   ON question_kp (content_hash);
CREATE INDEX IF NOT EXISTS ix_question_kp_tenant ON question_kp (tenant_id);
COMMENT ON TABLE question_kp IS '题↔知识点绑定（M3-3）；kp_code 只能写已存在的 DAG code';

CREATE TABLE IF NOT EXISTS exam_solutions (
    id                 BIGSERIAL PRIMARY KEY,
    tenant_id          VARCHAR(64),
    paper_id           BIGINT NOT NULL,
    paper_question_id  BIGINT NOT NULL,
    solution_steps     JSON,
    prompt_version     INTEGER,
    model              VARCHAR(128),
    generated_at       TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_exam_solution UNIQUE (paper_id, paper_question_id)
);
CREATE INDEX IF NOT EXISTS ix_exam_solution_paper ON exam_solutions (paper_id);
COMMENT ON TABLE exam_solutions IS '按卷题缓存的解析（M3-1）；同 key 不重复生成';

CREATE TABLE IF NOT EXISTS course_source (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64) NOT NULL,
    name        VARCHAR(255) NOT NULL,
    source_type VARCHAR(16) NOT NULL DEFAULT 'file',   -- file/url/bank_pack
    uri         TEXT NOT NULL,
    parser      VARCHAR(32) NOT NULL DEFAULT 'auto',   -- auto/textbook/outline/video/web/bank
    status      VARCHAR(16) NOT NULL DEFAULT 'paused', -- active/paused
    ingested_at TIMESTAMPTZ,
    created_by  VARCHAR(64),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_course_source_tenant ON course_source (tenant_id, status);

CREATE TABLE IF NOT EXISTS crawl_job (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     VARCHAR(64) NOT NULL,
    source_id     BIGINT,
    name          VARCHAR(255) NOT NULL,
    path          VARCHAR(16) NOT NULL DEFAULT 'P2',   -- P1 整卷入库 / P2 拆题入库
    schedule      VARCHAR(64),                          -- cron（5 段）；空 = 仅手动触发
    budget_limit  INTEGER NOT NULL DEFAULT 100,         -- 允许入库的条目上限
    spent         INTEGER NOT NULL DEFAULT 0,
    status        VARCHAR(16) NOT NULL DEFAULT 'paused',-- active/paused/stopped
    last_run_at   TIMESTAMPTZ,
    next_run_at   TIMESTAMPTZ,
    created_by    VARCHAR(64),
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_crawl_job_tenant ON crawl_job (tenant_id, status);

CREATE TABLE IF NOT EXISTS crawl_run (
    id            BIGSERIAL PRIMARY KEY,
    job_id        BIGINT NOT NULL,
    tenant_id     VARCHAR(64),
    status        VARCHAR(16) NOT NULL DEFAULT 'running', -- running/ok/failed/stopped
    started_at    TIMESTAMPTZ DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    fetched       INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    duplicated    INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    budget_stop   BOOLEAN NOT NULL DEFAULT false,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS ix_crawl_run_job ON crawl_run (job_id, started_at);
COMMENT ON TABLE crawl_run IS '采集运行日志（M3-8/M5-6）：成功/去重/失败计数，可重试';

CREATE TABLE IF NOT EXISTS kp_node (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   VARCHAR(64),
    code        VARCHAR(32) NOT NULL,
    course_id   VARCHAR(64),
    parent_code VARCHAR(32),
    name        VARCHAR(255) NOT NULL,
    subject     VARCHAR(32),
    grade       VARCHAR(32),
    source      VARCHAR(16) NOT NULL DEFAULT 'crawled', -- crawled/llm/manual
    status      VARCHAR(16) NOT NULL DEFAULT 'draft',   -- draft/published
    created_at  TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_kp_node_code UNIQUE (code)
);
CREATE INDEX IF NOT EXISTS ix_kp_node_status ON kp_node (status);
COMMENT ON TABLE kp_node IS '候选知识点（M5-3）：draft 不进入学生端图谱，审核后写入 kb_knowledge_points';
