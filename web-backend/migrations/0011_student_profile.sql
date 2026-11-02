-- M0-3 学生年级归属（2026-11-02）
--
-- 方案书 A11 / §6-12 定案：**不建班级、不并入 sys_user**。学生身份继续沿用
-- 裸字符串 student_id，这里只补一张轻量表承载年级，供按年级段下发试卷过滤。
--
-- 幂等：CREATE TABLE / INDEX IF NOT EXISTS，可重复执行。
-- 注意：web-backend 启动时 Base.metadata.create_all 也会建出该表（含 ORM 索引），
--       本脚本用于**线上已有库**补表（create_all 只建缺失表，不改老表）。

CREATE TABLE IF NOT EXISTS student_profile (
    id          BIGSERIAL PRIMARY KEY,
    student_id  VARCHAR(64) NOT NULL,
    tenant_id   VARCHAR(64) NOT NULL,
    grade       VARCHAR(32) NOT NULL,           -- 高一 / 高二 / 高三（写入前已归一）
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_student_profile UNIQUE (student_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_student_profile_student ON student_profile (student_id);
CREATE INDEX IF NOT EXISTS ix_student_profile_tenant  ON student_profile (tenant_id);
CREATE INDEX IF NOT EXISTS ix_student_profile_grade   ON student_profile (grade);
-- 按 (租户, 年级) 出名单/统计是主查询形态
CREATE INDEX IF NOT EXISTS ix_student_profile_tenant_grade
    ON student_profile (tenant_id, grade);

COMMENT ON TABLE  student_profile IS '学生年级归属（M0-3）；不建班级、不并入 sys_user';
COMMENT ON COLUMN student_profile.grade IS '高一/高二/高三，写入前经 normalize_grade 归一';

-- 存量兜底：现有学生（散落在错题/掌握度等表）统一归到试点校默认年级「高三」，
-- 与首期 MVP 题库（只有高三）一致。ON CONFLICT DO NOTHING 保证重跑不覆盖教师后来的修正。
INSERT INTO student_profile (student_id, tenant_id, grade)
SELECT DISTINCT student_id, 'school_pilot_01', '高三'
FROM (
    SELECT student_id FROM student_mastery WHERE student_id IS NOT NULL
    UNION
    SELECT student_id FROM wrong_questions WHERE student_id IS NOT NULL
    UNION
    SELECT student_id FROM drill_cards WHERE student_id IS NOT NULL
) s
WHERE student_id <> ''
ON CONFLICT (student_id, tenant_id) DO NOTHING;
