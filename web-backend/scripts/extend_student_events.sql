-- ============================================================================
-- S3 ⑤ student_events 扩展：judge v2 步骤级归因载荷
-- 对齐《步骤级归因落地计划书》S3 关键任务⑤：
--   emit_student_event 载荷扩展 first_wrong_step / error_type /
--   attribution_source / evidence（Kafka 向后兼容，旧消费者忽略新字段）。
--
-- 为什么要单独写这条迁移：
--   web-backend 用 Base.metadata.create_all 建表，该方法**只建缺失的表，不会给
--   已有表加列**。student_events 已存在于 106 库，故新增字段必须显式 ALTER。
--
-- 依赖：student_events（已存在）、student_step_errors（S1 迁移已建）
-- 全部幂等：可重复执行。
-- ============================================================================

ALTER TABLE student_events
  ADD COLUMN IF NOT EXISTS content_hash       VARCHAR(64),   -- 命中的标注题（归因归属）
  ADD COLUMN IF NOT EXISTS correct            BOOLEAN,       -- 判分结论；未提交作答为 NULL
  ADD COLUMN IF NOT EXISTS partial            BOOLEAN,
  ADD COLUMN IF NOT EXISTS first_wrong_step   INTEGER,       -- 首个出错步骤 step_no
  ADD COLUMN IF NOT EXISTS error_type         VARCHAR(16),   -- taxonomy 七类 + unknown
  ADD COLUMN IF NOT EXISTS attribution_source VARCHAR(8),    -- map / llm / unknown
  ADD COLUMN IF NOT EXISTS evidence           TEXT;          -- 支撑归因的学生作答片段

COMMENT ON COLUMN student_events.content_hash IS
  '命中的 course_kb.content_hash；judge v2 归因归属的题目';
COMMENT ON COLUMN student_events.first_wrong_step IS
  'judge v2 判定的首个出错步骤（course_kb_steps.step_no）';
COMMENT ON COLUMN student_events.error_type IS
  '错因七类：concept/formula/case/calc/read/logic/strategy；unknown=低置信待人工复核';
COMMENT ON COLUMN student_events.attribution_source IS
  '归因来源：map=客观题查表零token / llm=模型判定 / unknown=待人工复核';

-- 错因按类型聚合（S5 老师端错因分布）与按题回溯
CREATE INDEX IF NOT EXISTS idx_student_events_error_type
  ON student_events(error_type);
CREATE INDEX IF NOT EXISTS idx_student_events_content_hash
  ON student_events(content_hash);

-- 步骤级错因流水补索引：S5 周报按 学生×时间 / 错因类型 扫描
CREATE INDEX IF NOT EXISTS idx_student_step_errors_student_time
  ON student_step_errors(student_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_student_step_errors_error_type
  ON student_step_errors(error_type);
