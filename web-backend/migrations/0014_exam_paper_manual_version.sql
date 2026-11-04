-- M4-1 / M4-5 手动出卷、分值、版本与复用（2026-11-03）
--
-- 背景：
--   * M4-1 教师手动出卷需要「每题分值」与「卷面总分」，原 exam_paper_questions 没有 score；
--   * M4-5 试卷版本与复用需要 exam_papers.version 与 source，复制时新 id 引用同一
--     question_id/content_hash，但快照独立（改源卷不影响已下发卷判据）。
--
-- 幂等：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，可重复执行。
-- 红线：web-backend 启动期 Base.metadata.create_all **只建缺失表、不加列**，
--       线上老库必须手工执行本脚本，否则 ORM 查询会因缺列 500。

ALTER TABLE exam_papers ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE exam_papers ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'manual';

ALTER TABLE exam_paper_questions ADD COLUMN IF NOT EXISTS question_id BIGINT;
ALTER TABLE exam_paper_questions ADD COLUMN IF NOT EXISTS score NUMERIC(6,2);

COMMENT ON COLUMN exam_papers.version IS '试卷版本号（M4-5）；复制/另存为新卷时重置为 1，原卷版本自增';
COMMENT ON COLUMN exam_papers.source  IS '卷来源：manual 手动 / rule 规则组卷 / copy 复制 / auto_crawl 采集整卷 / llm 生成草稿';
COMMENT ON COLUMN exam_paper_questions.question_id IS '引用 course_kb.id（可空：教师直接录入的题无题库来源）';
COMMENT ON COLUMN exam_paper_questions.score IS '本题分值；NULL 表示未配分，发布前须补齐';

CREATE INDEX IF NOT EXISTS ix_exam_paper_tenant_source ON exam_papers (tenant_id, source);
CREATE INDEX IF NOT EXISTS ix_epq_question ON exam_paper_questions (question_id);