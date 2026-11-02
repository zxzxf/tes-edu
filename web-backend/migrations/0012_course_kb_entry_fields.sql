-- M0-1 教师录题新增字段（2026-11-03）
--
-- 背景：教师端录题页需要承载「答案预展开值 / 选择细分 / 空位数」，
-- 这三列在 course_kb 上原本没有。与 M0-4 的 ALTER 清单**同名同类型**，
-- 先在这里补齐（IF NOT EXISTS 幂等），M0-4 执行到同样的语句时会变成空操作。
--
-- 红线提醒：web-backend 启动时的 Base.metadata.create_all **只建缺失表、不加列**，
-- 线上老库必须手工跑本脚本，否则 ORM 查询会因缺列而 500。
--
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。

ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS answer_values JSONB;
ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS choice_type   VARCHAR(16);
ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS blank_count   INTEGER;

COMMENT ON COLUMN course_kb.answer_values IS '标答预展开的可接受答案值（M2-4 判分口径，JSONB 数组）';
COMMENT ON COLUMN course_kb.choice_type    IS 'single/multi；仅选择题有值，用于把「选择」细分到单选/多选';
COMMENT ON COLUMN course_kb.blank_count    IS '填空题空位数；非填空题为 NULL';

-- 教师端「我的草稿 / 待审」主查询形态：按租户 + 状态筛
CREATE INDEX IF NOT EXISTS ix_course_kb_tenant_status ON course_kb (tenant_id, status);
