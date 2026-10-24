-- ============================================================================
-- S5 ④ 错题本关联步骤：wrong_questions 补 content_hash
-- 目的：错题本要展示「这一步的正确做法(correction)」与知识点卡，
--       必须能从错题定位到 course_kb_steps 的具体步骤，
--       而 wrong_questions 原来只有 question(text) 没有规范引用(content_hash)。
-- 幂等，可重复执行。在 106 执行：
--   docker exec -i srv-postgres psql -U tes -d tes_edu -f extend_wrong_questions.sql
-- ============================================================================

ALTER TABLE wrong_questions
  ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS ix_wq_content_hash
  ON wrong_questions (content_hash) WHERE content_hash IS NOT NULL;

COMMENT ON COLUMN wrong_questions.content_hash IS
'题目规范引用 → course_kb.content_hash / course_kb_steps.content_hash。'
'有值时错题本可按 (content_hash, wrong_step_no) 取到该步 correction 与知识点卡；'
'无值（历史数据未回填）时退化为只展示题干与原有解析。';
