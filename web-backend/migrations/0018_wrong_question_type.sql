-- 错题本按题型筛选（选择题/填空题/解答题），2026-11-05
--
-- wrong_questions 新增 question_type（choice/blank/subjective），
-- 来源卷的错题回填卷题快照里的 qtype（判分链路以后也会在写入时带上）。
-- 线上库是 Postgres，迁移手工跑一次即可；幂等。

ALTER TABLE wrong_questions
    ADD COLUMN IF NOT EXISTS question_type VARCHAR(16);

-- 回填：exam 来源、且有卷题关联的错题，从卷题快照取题型
UPDATE wrong_questions w
SET question_type = q.qtype
FROM exam_paper_questions q
WHERE w.source = 'exam'
  AND w.paper_question_id IS NOT NULL
  AND w.paper_question_id = q.id
  AND w.question_type IS NULL;

COMMENT ON COLUMN wrong_questions.question_type IS '题型：choice/blank/subjective，供错题本按题型筛选';
