-- 步骤级归因·教学深度扩展（2026-10-23，用户细化：每步带详解/知识点串联/理解/记忆/触类旁通）
-- 在 106 执行：docker exec -i srv-postgres psql -U tes -d tes_edu -f < 文件
-- 幂等，可重复执行。

-- 1) 知识点层：跨题复用，生成一次 435 个即可被所有题共享
ALTER TABLE kb_knowledge_points
    ADD COLUMN IF NOT EXISTS understanding text,   -- 知识点如何理解（学生视角 + 教师视角）
    ADD COLUMN IF NOT EXISTS memory       text,    -- 记忆方法（口诀/联想/记忆锚点）
    ADD COLUMN IF NOT EXISTS analogy       text;    -- 触类旁通（类比迁移到相似题型/相关知识点）

COMMENT ON COLUMN kb_knowledge_points.understanding IS '知识点教学讲解：是什么、为什么这样定义、与已学知识的联系';
COMMENT ON COLUMN kb_knowledge_points.memory       IS '记忆方法：口诀/联想/记忆宫殿锚点，帮助长期记忆';
COMMENT ON COLUMN kb_knowledge_points.analogy       IS '触类旁通：类比迁移示例，迁移到相似题型';

-- 2) 步骤层：每步带详解（推导过程 + 知识点串联）与常见错误原因
ALTER TABLE course_kb_steps
    ADD COLUMN IF NOT EXISTS explanation text,   -- 详解：推导过程 + 知识点串联（为什么这步走、关联哪个知识点）
    ADD COLUMN IF NOT EXISTS error_reason text;   -- 常见错误原因（这一步学生容易错在哪、为什么错、正确思路）

COMMENT ON COLUMN course_kb_steps.explanation IS '详解：解题推导过程 + 知识点串联';
COMMENT ON COLUMN course_kb_steps.error_reason  IS '该步骤常见错误原因（正确步骤此项可空）';
