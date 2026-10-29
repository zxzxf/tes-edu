-- M2-7 判分指标看板：逐题记录「这条结论由哪条判据给出」
--
-- 为什么必须落库而不是面板上现算：
--   * 现算要求把当时的标答、当时的 answer_values 全部复现一遍，而这两者会被
--     教师改动（追加答案值、修正标答）——事后重算得出的是「今天的机器会怎么判」，
--     不是「当时机器为什么这么判」，用它画趋势等于篡改历史。
--   * 冻结记录（M2-10）也是按标答指纹走的，标答一改就失效；判据来源必须跟着
--     判分当时落下来，才解释得清「覆盖率为什么在那天掉了」。
--
-- 编码见 app/core/grading_metrics.py（auto_* = 机器给出结论，manual_* = 转人工）。
-- 加列前的老数据为 NULL，指标里归入 unknown，不猜。

ALTER TABLE exam_submission_items
    ADD COLUMN IF NOT EXISTS grading_source VARCHAR(24);

CREATE INDEX IF NOT EXISTS ix_exam_submission_items_grading_source
    ON exam_submission_items (grading_source);

-- 指标按提交时间开窗，补一个索引避免全表扫（当前数据量小，属预防性）
CREATE INDEX IF NOT EXISTS ix_exam_submissions_submitted_at
    ON exam_submissions (submitted_at);
