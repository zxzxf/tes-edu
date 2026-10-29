-- 人工评定工单 + 答案值预展开（M2-4 / M2-5，2026-10-29）
--
-- 背景：M2-3 之后填空题已有三态判定，但「转人工」的题只是把
--       exam_submission_items.status 置成 manual_pending —— 谁来判、判没判、
--       判完怎么回写，全都没有落点，闭环缺最后一环。
--
-- 本迁移补三件事：
--   1. exam_paper_questions.answer_values —— 标答预展开成可接受答案值集合（M2-4），
--      组卷时写入；教师批改时确认「这种写法也算对」可追加。命中即判对，只救对不判错。
--   2. exam_grading_tickets   —— 转人工题一张工单（M2-5），承载待办/认领/判定。
--   3. exam_grading_revisions —— 改判留痕（M2-6 红线：成绩改动必须可追溯）。
--
-- 设计要点：
--   * 工单幂等：UNIQUE(submission_id, submission_item_id)，重复提交不会重复开单。
--   * 判定结果**回写** exam_submission_items.correct/status 与 exam_submissions 汇总，
--     学生端 GET /result 立刻可见（不额外维护第二份成绩）。
--   * 全部 IF NOT EXISTS，可重复执行。

ALTER TABLE exam_paper_questions
    ADD COLUMN IF NOT EXISTS answer_values JSON;

COMMENT ON COLUMN exam_paper_questions.answer_values
    IS '可接受答案值集合（M2-4），标答预展开 + 教师确认追加；命中即判对';

CREATE TABLE IF NOT EXISTS exam_grading_tickets (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64),
    submission_id       BIGINT NOT NULL,        -- exam_submissions.id
    submission_item_id  BIGINT NOT NULL,        -- exam_submission_items.id
    assignment_id       BIGINT NOT NULL,
    paper_id            BIGINT NOT NULL,
    paper_question_id   BIGINT NOT NULL,
    student_id          VARCHAR(64) NOT NULL,
    seq                 INTEGER NOT NULL,
    qtype               VARCHAR(16) NOT NULL,   -- blank/subjective
    student_answer      TEXT,
    reason              VARCHAR(255),           -- 为什么转人工（机器判不了的具体原因）
    status              VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/claimed/reviewed
    assignee            VARCHAR(64),            -- 认领教师
    claimed_at          TIMESTAMPTZ,
    reviewed_by         VARCHAR(64),
    reviewed_at         TIMESTAMPTZ,
    verdict             VARCHAR(16),            -- correct/partial/wrong
    comment             TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (submission_id, submission_item_id)
);
CREATE INDEX IF NOT EXISTS ix_exam_ticket_todo
    ON exam_grading_tickets (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_exam_ticket_assignment
    ON exam_grading_tickets (assignment_id, status);
CREATE INDEX IF NOT EXISTS ix_exam_ticket_student
    ON exam_grading_tickets (student_id);

CREATE TABLE IF NOT EXISTS exam_grading_revisions (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_id           BIGINT NOT NULL,
    submission_item_id  BIGINT NOT NULL,
    action              VARCHAR(16) NOT NULL,   -- claim/judge/rejudge/add_value
    actor_id            VARCHAR(64),
    actor_role          VARCHAR(16),
    before_correct      BOOLEAN,
    after_correct       BOOLEAN,
    before_partial      BOOLEAN,
    after_partial       BOOLEAN,
    before_status       VARCHAR(16),
    after_status        VARCHAR(16),
    comment             TEXT,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_exam_revision_ticket
    ON exam_grading_revisions (ticket_id, created_at);

COMMENT ON TABLE exam_grading_tickets IS '人工评定工单：转人工的题一张待办，判定后回写成绩';
COMMENT ON COLUMN exam_grading_tickets.status IS 'pending=待认领；claimed=已认领；reviewed=已判定';
COMMENT ON TABLE exam_grading_revisions IS '批改留痕：认领/判定/改判/追加答案值，成绩改动必须可追溯';
