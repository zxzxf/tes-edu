-- ============================================================================
-- T-C4 student_events 扩展：引导式对话埋点载荷（Hint 阶梯 / 题型路由 / 直接看解析）
-- 对齐《P2 引导式对话与横向收尾任务计划书》T-C4：
--   落地六项 P2 指标所需字段，使 hint_shown / direct_reveal / solve 事件的
--   Hint 阶梯状态、题型路由档、意图路由结果、挫败连击等不再被消费者丢弃。
--
-- 为什么单独迁移：web-backend 用 Base.metadata.create_all 建表，该方法只建缺失
-- 的表、不会给已有表加列；student_events 已存在于 106 库，新增字段必须显式 ALTER。
--
-- 依赖：student_events（已存在，judge v2 列已由 extend_student_events.sql 加好）
-- 全部幂等：可重复执行。
-- ============================================================================

ALTER TABLE student_events
  ADD COLUMN IF NOT EXISTS intent                  VARCHAR(16),    -- 意图路由结果: direct/guide/rule
  ADD COLUMN IF NOT EXISTS intent_source           VARCHAR(16),    -- rules/llm/default/explicit
  ADD COLUMN IF NOT EXISTS hint_level              INTEGER,        -- Hint 阶梯当前等级 0-4
  ADD COLUMN IF NOT EXISTS hint_level_name         VARCHAR(16),    -- L0..L4 名称
  ADD COLUMN IF NOT EXISTS hint_rounds             INTEGER,        -- 当前轮次
  ADD COLUMN IF NOT EXISTS hint_max_rounds         INTEGER,        -- 该题型轮次上限
  ADD COLUMN IF NOT EXISTS hint_step               INTEGER,        -- 分步拆解：当前步（压轴题）
  ADD COLUMN IF NOT EXISTS hint_step_total         INTEGER,        -- 总步数
  ADD COLUMN IF NOT EXISTS hint_frustration_streak INTEGER,        -- 连续挫败计数（>=2 视为挫败信号）
  ADD COLUMN IF NOT EXISTS hint_finale             BOOLEAN,        -- 是否压轴题
  ADD COLUMN IF NOT EXISTS hint_direct_reveal      BOOLEAN,        -- 是否直达 L4（主动/被动）
  ADD COLUMN IF NOT EXISTS question_tier           VARCHAR(16),    -- 题型路由档: memory/medium/finale
  ADD COLUMN IF NOT EXISTS question_tier_name      VARCHAR(16);

COMMENT ON COLUMN student_events.intent IS
  'T-C1 意图路由结果：direct=直给档 / guide=引导档 / rule=规则档；首轮直给率据此判定';
COMMENT ON COLUMN student_events.hint_level IS
  'T-C2 Hint 阶梯当前等级 0-4；hint_shown/direct_reveal 事件带，推进率据此判下一级';
COMMENT ON COLUMN student_events.hint_frustration_streak IS
  'T-C2 连续挫败信号计数；>=2 视为一次挫败信号（T-C4 挫败信号率分子）';
COMMENT ON COLUMN student_events.question_tier IS
  'T-C3 题型路由档：memory/medium/finale；分层聚合六项指标';

-- 题型路由档 + 意图路由结果聚合（T-C4 按题型/意图分层看六项指标）
CREATE INDEX IF NOT EXISTS idx_student_events_question_tier
  ON student_events(question_tier);
CREATE INDEX IF NOT EXISTS idx_student_events_intent
  ON student_events(intent);
