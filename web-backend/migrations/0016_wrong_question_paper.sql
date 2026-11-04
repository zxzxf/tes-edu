-- M3-9 错题按卷筛选（2026-11-03）
--
-- 判错自动进错题本时记录来源卷，学生端才能「按卷筛选 + 重做」。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。

ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS paper_id BIGINT;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS paper_question_id BIGINT;

COMMENT ON COLUMN wrong_questions.paper_id IS '来源试卷 id（source=exam 时有值），M3-9 按卷筛选';
COMMENT ON COLUMN wrong_questions.paper_question_id IS '来源卷题 id（exam_paper_questions.id），重做时定位';

CREATE INDEX IF NOT EXISTS ix_wrong_questions_paper ON wrong_questions (student_id, paper_id);