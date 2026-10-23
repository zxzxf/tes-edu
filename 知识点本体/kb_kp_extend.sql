-- kb_knowledge_points 扩表：让 435 点 DAG 的编码与前置关系可持久化
-- 说明：均为可空列 / 新增表，属于无损变更，可安全执行。
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS code varchar(32);          -- 知识点编码 MA-B?-C?-S?-K??
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS chapter varchar(64);       -- 章
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS section varchar(64);       -- 节
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS importance varchar(16);    -- core/std
ALTER TABLE kb_knowledge_points ADD COLUMN IF NOT EXISTS book varchar(32);          -- 册次
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_kp_code ON kb_knowledge_points(code);

-- 前置关系（DAG 边）单独建表，避免在知识点表里塞数组
CREATE TABLE IF NOT EXISTS kb_kp_prerequisites (
    kp_code        varchar(32) NOT NULL,
    prereq_code    varchar(32) NOT NULL,
    relation_type  varchar(16) DEFAULT 'prerequisite',
    created_at     timestamptz DEFAULT now(),
    PRIMARY KEY (kp_code, prereq_code)
);
CREATE INDEX IF NOT EXISTS idx_kb_kp_pre_prereq ON kb_kp_prerequisites(prereq_code);
