-- T-CAP4 语义去重（第一闸补盲）：记录「和哪一题相似」，供 B 端异常队列展示 dup_of。
-- 文本指纹闸的 dup_of 仍由 API 用倒排索引现算（兼容存量），语义闸命中的
-- 具体题号必须落库——余弦 TopK 结果无法从 guard_reason 可靠反解。
ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS guard_dup_of INTEGER;

COMMENT ON COLUMN course_kb.guard_dup_of IS
    'T-CAP4 第一闸判重指向的题 id（content_hash/文本指纹/语义近似）';
