-- 判分护栏：同一标答累计 K 次改判 → 冻结该题并告警（M2-10 / G3，2026-10-30）
--
-- 背景：改判是线上唯一可信的「机器判错了」信号。同一道题、同一份标答被教师
--       反复改判，说明标答（或它的可接受答案值集合）有问题——机器按它判会把
--       一批本答对的学生稳定地判错。只靠 manual_pending 兜不住：只要机器敢判，
--       就轮不到人工。攒够 K 次就必须把这道题的自动判分关掉。
--
-- 本迁移补两张表：
--   1. exam_answer_freeze    —— 当前护栏状态（观察计数 / 冻结 / 已解冻）。
--   2. exam_grading_alerts   —— 告警流水（冻结 / 复发 / 解冻），append-only。
--
-- 设计要点：
--   * 键是 (tenant_id, content_hash, answer_fingerprint)，**不是** paper_question_id。
--     exam_paper_questions 是随卷快照，同一道题每张卷一行；按行冻结只能护住出问题
--     的那一张卷，重新组卷又是新行、照样按错答案判。按题面身份跨卷生效。
--   * 叠加标答指纹：答案一改指纹就变，新指纹从 0 开始观察；旧指纹下的冻结记录
--     只保护仍在用旧快照的老卷。这是「同一答案累计 K 次改判」的字面含义。
--   * status：watching / frozen / released。解冻不删行，计数清零后可复发。
--   * 全部 IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS exam_answer_freeze (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64),
    content_hash        VARCHAR(64) NOT NULL,   -- 题面身份（跨卷）
    answer_fingerprint  VARCHAR(32) NOT NULL,   -- 标答归一化指纹（换答案即换键）
    answer              TEXT,                   -- 冻结时的标答快照（展示用）
    paper_id            BIGINT,                 -- 触发冻结的代表题所在卷
    paper_question_id   BIGINT,
    seq                 INTEGER,
    qtype               VARCHAR(16),
    flips               INTEGER NOT NULL DEFAULT 0,   -- 有效改判次数
    threshold           INTEGER NOT NULL DEFAULT 3,   -- 触发冻结的阈值 K
    status              VARCHAR(16) NOT NULL DEFAULT 'watching',
    actors              JSON,                   -- 改判过这道题的教师（去重）
    affected_count      INTEGER DEFAULT 0,      -- 已按该标答自动判错的提交数
    release_count       INTEGER DEFAULT 0,      -- 解冻次数（>0 说明复发过）
    acked_by            VARCHAR(64),            -- 告警已读人
    acked_at            TIMESTAMPTZ,
    frozen_at           TIMESTAMPTZ,
    released_at         TIMESTAMPTZ,
    released_by         VARCHAR(64),
    release_comment     TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    updated_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, content_hash, answer_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_exam_freeze_list
    ON exam_answer_freeze (tenant_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_exam_freeze_content
    ON exam_answer_freeze (tenant_id, content_hash, status);

CREATE TABLE IF NOT EXISTS exam_grading_alerts (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           VARCHAR(64),
    kind                VARCHAR(16) NOT NULL,   -- freeze/refreeze/release
    freeze_id           BIGINT,
    paper_id            BIGINT,
    paper_question_id   BIGINT,
    seq                 INTEGER,
    content_hash        VARCHAR(64),
    answer              TEXT,
    flips               INTEGER DEFAULT 0,
    threshold           INTEGER,
    affected_count      INTEGER DEFAULT 0,
    message             TEXT,
    detail              JSON,
    actor_id            VARCHAR(64),
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_exam_alert_tenant_time
    ON exam_grading_alerts (tenant_id, created_at DESC);

COMMENT ON TABLE exam_answer_freeze IS
    '判分护栏状态：同一标答累计 K 次改判即冻结该题自动判分，键为 (租户,题面,标答指纹)';
COMMENT ON COLUMN exam_answer_freeze.status IS
    'watching=观察中；frozen=已冻结（判分一律转人工）；released=已解冻（计数清零）';
COMMENT ON COLUMN exam_answer_freeze.affected_count IS
    '已按该标答自动判为错的提交数（不自动改历史成绩，仅提示影响面）';
COMMENT ON TABLE exam_grading_alerts IS
    '判分告警流水（append-only）：冻结/复发/解冻；当前状态见 exam_answer_freeze';
