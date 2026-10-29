-- 学生 Agent『作业闭环』MVP 试卷域（2026-10-29）
--
-- 5 张表：exam_papers / exam_paper_questions / exam_assignments /
--         exam_submissions / exam_submission_items
--
-- 设计要点：
--  * 答案随卷快照（exam_paper_questions.answer），判分唯一来源是『本卷答案』，
--    不依赖外部检索 / LLM（ADR-A10），故 deterministic 0-token 可判（web-backend/app/core/equiv.py）。
--  * 租户隔离 tenant_id；年级段下发（target_type=grade）替代班级（M0-3 后置，v1.3 定案）。
--  * 幂等：exam_submissions 唯一 (assignment_id, student_id)；可重复执行（IF NOT EXISTS）。
--  * MVP 只做『高三·数学·纯选择题卷』，paper_type 默认 'pure_choice'。
--  * 全部 IF NOT EXISTS / ADD COLUMN IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS exam_papers (
    id               BIGSERIAL PRIMARY KEY,
    tenant_id        VARCHAR(64),
    title            VARCHAR(255) NOT NULL,
    paper_type       VARCHAR(16)  NOT NULL DEFAULT 'pure_choice',  -- pure_choice/pure_blank/mixed
    grade            VARCHAR(32)  NOT NULL DEFAULT '高三',
    subject          VARCHAR(32)  NOT NULL DEFAULT '数学',
    difficulty_avg   SMALLINT,
    knowledge_points JSON,
    created_by       VARCHAR(64),
    published        BOOLEAN      NOT NULL DEFAULT false,
    published_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_exam_paper_tenant ON exam_papers (tenant_id);

CREATE TABLE IF NOT EXISTS exam_paper_questions (
    id               BIGSERIAL PRIMARY KEY,
    paper_id         BIGINT NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    content_hash     VARCHAR(64),              -- 关联 course_kb（教师录入可为空）
    qtype            VARCHAR(16) NOT NULL,     -- choice/blank/subjective
    multi            BOOLEAN NOT NULL DEFAULT false, -- 选择题是否多选
    stem             TEXT NOT NULL,
    options          JSON,                     -- {label: content} 选择题用
    answer           TEXT NOT NULL,            -- 本卷答案（判分唯一来源）
    analysis         TEXT,                     -- 解法+步骤
    difficulty       SMALLINT,
    knowledge_points JSON,
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (paper_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_epq_paper ON exam_paper_questions (paper_id);

CREATE TABLE IF NOT EXISTS exam_assignments (
    id             BIGSERIAL PRIMARY KEY,
    paper_id       BIGINT NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
    tenant_id      VARCHAR(64),
    target_type    VARCHAR(16) NOT NULL DEFAULT 'grade',  -- grade/student
    target_grade   VARCHAR(32),                           -- 高三
    target_student VARCHAR(64),                          -- target_type=student 时
    assigned_by    VARCHAR(64),
    assigned_at    TIMESTAMPTZ DEFAULT now(),
    due_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_exam_assign_grade
    ON exam_assignments (target_type, target_grade);
CREATE INDEX IF NOT EXISTS ix_exam_assign_student
    ON exam_assignments (target_type, target_student);

CREATE TABLE IF NOT EXISTS exam_submissions (
    id             BIGSERIAL PRIMARY KEY,
    assignment_id  BIGINT NOT NULL REFERENCES exam_assignments(id) ON DELETE CASCADE,
    paper_id       BIGINT NOT NULL,
    student_id     VARCHAR(64) NOT NULL,
    tenant_id      VARCHAR(64),
    client_id      VARCHAR(64),              -- 离线提交幂等键
    status         VARCHAR(16) NOT NULL DEFAULT 'graded', -- graded/manual_pending
    score          NUMERIC(6,2),            -- 正确题数
    correct_count  INTEGER,
    total_count    INTEGER,
    submitted_at   TIMESTAMPTZ DEFAULT now(),
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (assignment_id, student_id)      -- 每学生每卷一次提交
);
CREATE INDEX IF NOT EXISTS ix_exam_sub_student
    ON exam_submissions (student_id, assignment_id);

CREATE TABLE IF NOT EXISTS exam_submission_items (
    id                BIGSERIAL PRIMARY KEY,
    submission_id     BIGINT NOT NULL REFERENCES exam_submissions(id) ON DELETE CASCADE,
    paper_question_id BIGINT NOT NULL,      -- 关联 exam_paper_questions.id
    seq               INTEGER NOT NULL,
    qtype             VARCHAR(16) NOT NULL,
    student_answer    TEXT,
    correct           BOOLEAN,
    partial           BOOLEAN DEFAULT false, -- 多选漏选但所选皆正确
    status            VARCHAR(16) NOT NULL DEFAULT 'auto', -- auto/manual_pending
    created_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (submission_id, paper_question_id)
);
CREATE INDEX IF NOT EXISTS ix_exam_subitem_sub
    ON exam_submission_items (submission_id);

COMMENT ON TABLE exam_papers IS '试卷头：年级段/学科/题型/发布状态';
COMMENT ON COLUMN exam_paper_questions.answer IS '本卷答案（判分唯一来源），选择题为字母组合如 C / A,B';
COMMENT ON COLUMN exam_assignments.target_type IS 'grade=按年级段下发；student=指定学生';
COMMENT ON COLUMN exam_submissions.status IS 'graded=已自动判分；manual_pending=含待人工评定的题';
COMMENT ON COLUMN exam_submission_items.partial IS '多选漏选但所选皆正确（MVP 标记但不计分）';
