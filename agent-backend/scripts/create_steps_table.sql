-- 步骤级归因：全题型步骤表
-- 设计（2026-10-23，用户拍板"全题型不应该是部分题型"）：
--   course_kb 只有题级 knowledge_points(json)，无法支撑步骤级归因。
--   本表给每道题挂有序步骤，覆盖全部题型：
--     单选/多选 -> 每选项一步(option)，is_correct 标记正误项，支持按选项部分计分
--     填空      -> 每空一步(blank)，按答案顶层逗号/分号拆（括号感知）
--     解答      -> subq（数学人工细标 kp_source=manual）/ procedure（解析编号拆）
--     实验      -> procedure（解析编号拆）
--     兜底      -> single（全题一步），保证每题至少 1 步，归因永远有落点
--   kp_source: manual=人工细标知识点；question=继承题级主知识点（后续教研精标后升级）
-- 幂等，可重复执行。在 106 执行：docker exec srv-postgres psql -U tes -d tes_edu -f < 文件
CREATE TABLE IF NOT EXISTS course_kb_steps (
    content_hash text NOT NULL,
    step_no      integer NOT NULL,
    step_kind    text NOT NULL,            -- option/blank/subq/procedure/single
    label        text,                     -- A/B/C/D、第N空、(1)、全题
    text         text,                     -- 选项内容 / 小问文本 / 步骤描述
    is_correct   boolean,                  -- 仅 option 有值；其余 NULL
    kp_code      text,                     -- 知识点 id（数学 DAG id）；物理为 NULL
    kp_name      text,                     -- 知识点中文名（物理为章节域名）
    kp_source    text NOT NULL DEFAULT 'question',  -- manual/question
    confidence   text,                     -- high/mid/low，仅 manual 有值
    explanation  text,                     -- 详解：推导过程 + 知识点串联
    error_reason text,                     -- 常见错误原因（正确步骤可空）
    PRIMARY KEY (content_hash, step_no)
);
CREATE INDEX IF NOT EXISTS idx_steps_kp_code ON course_kb_steps(kp_code);
CREATE INDEX IF NOT EXISTS idx_steps_kind ON course_kb_steps(step_kind);
COMMENT ON TABLE course_kb_steps IS '全题型步骤表：步骤级归因基础（选项/空/小问/解析编号）';
