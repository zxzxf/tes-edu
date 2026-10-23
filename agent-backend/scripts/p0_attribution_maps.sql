-- ============================================================================
-- P0 步骤级归因映射结构迁移
-- 对齐《步骤级归因落地计划书》2026-10-23 用户拍板口径：
--   ① 数据模型采用独立宽表 course_kb_steps（非 Schema v1 原 JSONB 方案）
--   ② 知识点字段 understanding/memory/analogy（prerequisites 复用 kb_kp_prerequisites）
--   ③ 客观题 distractor_map / 填空 wrong_value_map / 解答 typical_errors
--   ④ wrong_questions 扩展 + 新表 student_step_errors（采集落点）
-- 依赖：course_kb_steps（已建）、wrong_questions（已存在）、
--       kb_knowledge_points（已加 understanding/memory/analogy）
-- 本迁移只建"结构"（S3 查表燃料容器），内容填充见 P1.5（LLM 生成器）。
-- 全部幂等：可重复执行。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. course_kb_steps 补齐三类归因映射列（S3 零成本查表燃料）
--    采用宽表方案：每步一行，映射分析直接挂在对应 step 行上。
-- ----------------------------------------------------------------------------

ALTER TABLE course_kb_steps
  ADD COLUMN IF NOT EXISTS distractor_map JSONB,   -- 仅 step_kind='option' 且 is_correct=false 使用
  ADD COLUMN IF NOT EXISTS wrong_value_map JSONB,  -- 仅 step_kind='blank' 使用
  ADD COLUMN IF NOT EXISTS typical_errors  JSONB;  -- 仅 step_kind IN ('procedure','subq') 使用

COMMENT ON COLUMN course_kb_steps.distractor_map IS
'选择题错项诱惑分析。仅用于 step_kind=''option'' 且 is_correct=false 的行。'
'结构：{"wrong_step":<int>,"error_type":"<taxonomy七类>","reason":"<认知根源>","correction":"<正确做法一句话>"}'
'示例：{"wrong_step":2,"error_type":"concept","reason":"把中位数与均值混淆","correction":"中位数看位置不看数值大小"}';

COMMENT ON COLUMN course_kb_steps.wrong_value_map IS
'填空题典型错值分析。仅用于 step_kind=''blank'' 的行。JSON 数组，每个典型错值一条。'
'结构：[{"value":"<归一化错值>","wrong_step":<int>,"error_type":"<七类>","reason":"<根源>","correction":"<正确>"}]'
'未命中映射的填空错值 → judge v2 落 LLM 兜底归因(attribution_source=llm)。';

COMMENT ON COLUMN course_kb_steps.typical_errors IS
'解答/小题典型错误。仅用于 step_kind IN (''procedure'',''subq'') 的行。JSON 数组。'
'结构：[{"error_type":"<七类>","reason":"<这步为何会错>","correction":"<这步正确写法，可直接展示>","evidence_hint":"<判分线索>"}]';

-- JSONB GIN 索引：支撑 judge v2 / P3 按 error_type 反查（partial 索引跳过 NULL 行）
CREATE INDEX IF NOT EXISTS ix_ckbs_distractor_map ON course_kb_steps USING gin(distractor_map) WHERE distractor_map IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ckbs_wrong_value_map ON course_kb_steps USING gin(wrong_value_map) WHERE wrong_value_map IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_ckbs_typical_errors  ON course_kb_steps USING gin(typical_errors)  WHERE typical_errors  IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 2. wrong_questions 扩展（采集落点增强）
--    说明：wrong_questions 以 question(text) 为引用粒度（历史表），
--          此处仅补充归因落点列，不动原结构与引用方式。
-- ----------------------------------------------------------------------------

ALTER TABLE wrong_questions
  ADD COLUMN IF NOT EXISTS wrong_step_no        SMALLINT,
  ADD COLUMN IF NOT EXISTS error_type           VARCHAR(16),
  ADD COLUMN IF NOT EXISTS evidence             TEXT,
  ADD COLUMN IF NOT EXISTS attribution_source   VARCHAR(8);   -- table | llm | unknown

COMMENT ON COLUMN wrong_questions.wrong_step_no      IS '该错题断在 course_kb_steps 的第几步';
COMMENT ON COLUMN wrong_questions.error_type         IS 'taxonomy 七类之一（concept/formula/case/calc/read/logic/strategy）';
COMMENT ON COLUMN wrong_questions.evidence           IS '判分证据（学生作答特征 / LLM 判读摘要）';
COMMENT ON COLUMN wrong_questions.attribution_source IS '归因来源：table=查表命中 / llm=LLM兜底 / unknown=未归因';

-- ----------------------------------------------------------------------------
-- 3. 新表 student_step_errors（过程采集落点，S4 使用）
--    以 content_hash 为题目规范引用（与 course_kb / course_kb_steps 一致），
--    与 wrong_questions(question text) 形成"规范引用 + 原始文本"双轨。
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS student_step_errors (
  id                 BIGSERIAL PRIMARY KEY,
  tenant_id          VARCHAR(32),
  student_id         VARCHAR(64) NOT NULL,
  content_hash       VARCHAR(64) NOT NULL,         -- → course_kb.content_hash
  step_no            SMALLINT    NOT NULL,
  error_type         VARCHAR(16),                  -- 与映射 JSONB 内 error_type 对齐(taxonomy七类)
  attribution_source VARCHAR(8),                   -- table | llm | manual
  evidence           TEXT,
  occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_sse_student_time ON student_step_errors (student_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_sse_question     ON student_step_errors (content_hash, step_no);
CREATE INDEX IF NOT EXISTS ix_sse_error_type   ON student_step_errors (error_type);

COMMENT ON TABLE student_step_errors IS '学生步骤级错误流水：每次判分落一条，支撑 S4 过程采集 + P3 掌握度图谱沿 kb_kp_prerequisites DAG 回溯';
