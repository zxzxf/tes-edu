-- 0020 错题本选项
-- wrong_questions 新增 options 列，并回填 exam 来源选择题的选项快照。
-- 选项统一存成 [{"label","content"}] 的 JSON 字符串，与 App 端 ExamOption 对齐。
-- 旧代码忽略该列，加列安全；回填只动 options 为空的行，不触碰已有数据。

ALTER TABLE wrong_questions ADD COLUMN IF NOT EXISTS options TEXT;

-- 回填：exam 来源、能经 paper_question_id 关联到卷题快照、且 options 为空的行。
-- 卷题 options 为**数组形态**（如 ['A. {2}', 'B. {2,3}', 'C. {3,4}', 'D. {2,3,4}']）：
-- 逐元素展开，label 按序号 A/B/C/D 推导，content 保留原始文本（含 "A. " 前缀），
-- 与结果页 ExamOption 的形状一致（结果页也是同样的 array→[{label,content}] 处理）。
-- 同时提 updated_at：错题增量拉取按 updated_at>=since 过滤，不提档已同步设备拉不到新选项。
UPDATE wrong_questions w
SET options = sub.opt_json,
    updated_at = now()
FROM (
    SELECT epq.id AS pq_id,
           (SELECT jsonb_agg(jsonb_build_object('label', chr((64 + t.idx)::int), 'content', t.val))::text
            FROM jsonb_array_elements_text(epq.options::jsonb) WITH ORDINALITY AS t(val, idx)) AS opt_json
    FROM exam_paper_questions epq
    WHERE epq.options IS NOT NULL
      AND jsonb_typeof(epq.options::jsonb) = 'array'
) sub
WHERE w.source = 'exam'
  AND w.paper_question_id = sub.pq_id
  AND w.options IS NULL
  AND sub.opt_json IS NOT NULL;

COMMENT ON COLUMN wrong_questions.options IS '选择题选项：标准化 JSON 字符串 [{"label","content"}]，与卷题快照同源';
