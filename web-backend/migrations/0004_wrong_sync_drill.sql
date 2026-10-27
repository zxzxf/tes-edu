-- 学生端错题本云同步 + 背诵卡间隔复习（2026-10-28）
--
-- 1) wrong_questions 补同步与展示所需列：
--    client_id  客户端生成 UUID，用于幂等 upsert（离线新建的题重复上传不得变两条）
--    updated_at 冲突判定（客户端与服务端比大小，大者胜）
--    deleted_at 软删：客户端同步时要能感知"服务端已删"，不能只靠物理删除
--    comment / correction / first_wrong_step 判分归因展示（App 错因卡用）
-- 2) drill_cards / drill_reviews：背诵卡排程（SM-2）与复习流水。
-- 幂等：全部 IF NOT EXISTS / ADD COLUMN IF NOT EXISTS，可重复执行。

ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS client_id VARCHAR(64);
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS first_wrong_step INTEGER;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS comment TEXT;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS correction TEXT;
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS source VARCHAR(16);

-- 幂等键：同一学生 + 同一 client_id 只允许一条未删除记录
CREATE UNIQUE INDEX IF NOT EXISTS ux_wrong_client
    ON wrong_questions (student_id, client_id)
    WHERE client_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_wrong_sync
    ON wrong_questions (student_id, updated_at);
CREATE INDEX IF NOT EXISTS ix_wrong_et
    ON wrong_questions (student_id, error_type);

CREATE TABLE IF NOT EXISTS drill_cards (
    id            BIGSERIAL PRIMARY KEY,
    student_id    VARCHAR(64) NOT NULL,
    tenant_id     VARCHAR(64),
    client_id     VARCHAR(64),
    kp            VARCHAR(128),
    front         TEXT NOT NULL,
    back          TEXT NOT NULL,
    source        VARCHAR(16),
    -- SM-2 排程三件套：难度系数 / 当前间隔 / 下次到期
    ease          DOUBLE PRECISION DEFAULT 2.5,
    interval_days DOUBLE PRECISION DEFAULT 0,
    due_at        TIMESTAMPTZ,
    reps          INTEGER DEFAULT 0,
    lapses        INTEGER DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_drill_due
    ON drill_cards (student_id, due_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_drill_client
    ON drill_cards (student_id, client_id)
    WHERE client_id IS NOT NULL AND deleted_at IS NULL;

-- 复习流水：离线复习会补传，rated_at 用**客户端**复习时间（不是写入时间）
CREATE TABLE IF NOT EXISTS drill_reviews (
    id         BIGSERIAL PRIMARY KEY,
    student_id VARCHAR(64) NOT NULL,
    card_id    BIGINT,
    client_id  VARCHAR(64),
    rating     SMALLINT NOT NULL,
    rated_at   TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_drill_review_card
    ON drill_reviews (student_id, card_id);

COMMENT ON COLUMN wrong_questions.client_id IS '客户端 UUID，同步幂等键';
COMMENT ON COLUMN wrong_questions.deleted_at IS '软删时间；同步时下发 tombstone，客户端据此本地删除';
COMMENT ON COLUMN drill_cards.ease IS 'SM-2 难度系数，默认 2.5，下限 1.3';
COMMENT ON COLUMN drill_cards.interval_days IS '当前复习间隔（天）；0 表示新卡当天到期';
