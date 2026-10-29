-- M2-9 / G1 答案双源交叉校验
--
-- 目的：把「答案是错的」拦在**发布之前**。判分的唯一依据是 exam_paper_questions.answer，
-- 它错一次，整卷学生就被稳定地判错一次，而 M2-10 的冻结护栏是事后止损（已经判错 K 次）。
--
-- 为什么落库而不是每次现算：
--   教师「我确认过这道题的答案」本身是一条数据（谁/何时/凭什么），必须能跨刷新存活。
--   代价是答案一改旧确认必须失效 —— run_checks 用 answer 列做锚点比对，不一致即回落待确认。
--
-- status：verified（双源一致）/ mechanical_pass（代入检验通过）/ teacher_confirmed（教师确认）
--         / conflict（双源冲突）/ unverified（待确认）。只有前三种可以发布。
-- 当前唯一的自动第二来源是「题库现行答案 ≠ 卷面快照」（同源一致不算验证）。

CREATE TABLE IF NOT EXISTS exam_answer_checks (
    id                  SERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64),
    paper_id            INTEGER NOT NULL,
    paper_question_id   INTEGER NOT NULL,
    seq                 INTEGER NOT NULL,
    qtype               VARCHAR(16),
    answer              TEXT,
    status              VARCHAR(24) NOT NULL DEFAULT 'unverified',
    secondary_source    VARCHAR(16),
    secondary_answer    TEXT,
    mechanical_code     VARCHAR(24),
    mechanical_detail   TEXT,
    suspect             BOOLEAN DEFAULT FALSE,
    llm_model           VARCHAR(64),
    llm_answer          TEXT,
    llm_agree           BOOLEAN,
    detail              TEXT,
    confirmed_by        VARCHAR(64),
    confirmed_at        TIMESTAMPTZ,
    confirmed_comment   TEXT,
    checked_at          TIMESTAMPTZ DEFAULT now(),
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_answer_check_question UNIQUE (paper_question_id)
);

CREATE INDEX IF NOT EXISTS ix_exam_answer_checks_tenant ON exam_answer_checks (tenant_id);
CREATE INDEX IF NOT EXISTS ix_exam_answer_checks_paper ON exam_answer_checks (paper_id);
CREATE INDEX IF NOT EXISTS ix_exam_answer_checks_status ON exam_answer_checks (status);
