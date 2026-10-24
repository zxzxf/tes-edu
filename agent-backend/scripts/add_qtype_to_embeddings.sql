-- T-C3 题型路由：course_kb_embeddings 补 type 列并从 course_kb 回填
-- 背景：题型路由（qtype.route_question_tier）需要 course_kb.type（单选/多选/
--       填空/解答/实验），检索 chunks 原先不带该字段；本迁移补齐并一次性回填。
-- 幂等，可重复执行。在 106 执行（或经 137 隧道）：
--   docker exec srv-postgres psql -U tes -d tes_edu -f < 文件
ALTER TABLE course_kb_embeddings ADD COLUMN IF NOT EXISTS type text NOT NULL DEFAULT '';

-- 回填：按 content_hash 关联 course_kb（唯一键，ON CONFLICT DO NOTHING 保证一致）
UPDATE course_kb_embeddings e
SET type = c.type
FROM course_kb c
WHERE e.content_hash = c.content_hash
  AND (e.type IS NULL OR e.type = '');

-- 校验（预期回填行数 = 非 demo 检索行数，当前 211）：
-- SELECT count(*) FILTER (WHERE type <> '') AS filled, count(*) AS total
-- FROM course_kb_embeddings WHERE is_demo IS NOT TRUE;
