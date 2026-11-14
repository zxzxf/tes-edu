-- 0023_wrong_question_tenant.sql
-- 错题本补租户归属 + 后台列表租户隔离（2026-11-14）。
--
-- 背景：wrong_questions 自 0004 建表起就没有 tenant_id，ops.list_wrong 只按
--       student_id + 软删过滤。后台 /wrong-questions 允许教师/管理员/家长传
--       任意 student_id，等于跨租户可查别校学生的错题。本次加列并把列表
--       锁到登录态租户（平台级 admin/region_admin 仍跨租户只读，与
--       compliance / analytics 口径一致）。
--
-- 回填来源（2026-11-14 生产 DRY：48 行 / exam 34 / app 10 / source NULL 4）：
--   1. exam 来源有 paper_id → 卷所属租户（24 行命中 school_pilot_01）；
--   2. 其余行按该学生最近一次 exam_submissions.tenant_id；
--   3. 仍无归属的（u_admin / u_test 等演示账号）归一到兜底租户。
--   线上 exam_papers / exam_submissions 的租户除 1 张 demo 卷外全是
--   school_pilot_01，demo 卷没有关联错题，所以回填结果确定。
--
-- 幂等：备份表/加列/索引都 IF NOT EXISTS，三条 UPDATE 只动 tenant_id IS NULL 的行。
-- 回滚：wrong_questions_bak_20261114 保存**加列前**的完整原表；回滚时先删列再按需还原。
-- 执行：容器无 psql，SQL 逐条执行（asyncpg 不支持一次多语句），见部署记录。
-- 注意：tenant_id 不在 App 同步契约 wrong_out 里，回填**不提 updated_at**
--       （避免手机端全量重拉）；若将来把它加进 App 契约，必须同步提时间戳。

-- 1) 加列前的完整快照（必须放在 ADD COLUMN 之前）
CREATE TABLE IF NOT EXISTS wrong_questions_bak_20261114 AS
SELECT * FROM wrong_questions;

-- 2) 加列 + 默认值（漏写的 INSERT 自动落兜底租户，不会再产生 NULL 行）+ 索引
ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(64);
ALTER TABLE wrong_questions ALTER COLUMN tenant_id SET DEFAULT 'school_pilot_01';
CREATE INDEX IF NOT EXISTS ix_wrong_questions_tenant_id ON wrong_questions (tenant_id);

-- 3) 回填一：exam 来源按卷归属（paper_id → exam_papers.tenant_id）
UPDATE wrong_questions w
SET tenant_id = p.tenant_id
FROM exam_papers p
WHERE w.paper_id = p.id
  AND w.tenant_id IS NULL
  AND p.tenant_id IS NOT NULL
  AND p.tenant_id <> 'platform';

-- 4) 回填二：其余行按该学生最近一次提交的租户
UPDATE wrong_questions w
SET tenant_id = s.tenant_id
FROM (
    SELECT student_id, tenant_id,
           row_number() OVER (
               PARTITION BY student_id
               ORDER BY submitted_at DESC NULLS LAST, id DESC
           ) AS rn
    FROM exam_submissions
    WHERE tenant_id IS NOT NULL AND tenant_id <> 'platform'
) s
WHERE s.rn = 1
  AND s.student_id = w.student_id
  AND w.tenant_id IS NULL;

-- 5) 兜底：剩余无归属行一律归一到试点校（演示账号产生的错题）
UPDATE wrong_questions w
SET tenant_id = 'school_pilot_01'
WHERE w.tenant_id IS NULL;
