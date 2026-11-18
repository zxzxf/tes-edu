-- M1-1 / M1-7 补齐卡面子项（2026-11-18）
--
-- 1) blank_results（M1-1 卡面声明列）：exam_submission_items 逐空判定明细。
--    blank_judge 的逐空结论（correct/wrong/manual）此前聚合后即丢，
--    结果回看 / M3-9 重做只有整题粒度。落库形态：
--    [{idx, student, expected, verdict}]
--
-- 2) blanks（M1-7 异步编排前置）：学生逐空原始提交的落库。
--    提交即落原始作答（status=pending），判分环节（同步或后台）据此判题；
--    没有这列，后台判分只能靠 display 串反切分，「漏答某空」的空串会丢。
--
-- 类型说明：卡面写 jsonb，本表既有 JSON 列（answer_values/options 等）
-- 一律用 SQLAlchemy generic JSON（PG 落 json、sqlite 测试库兼容），
-- 保持一致用 JSON；无按键索引需求，jsonb 收益为零、代价是测试库不兼容。
--
-- judge_source 不在此建——0008 的 grading_source 即其等价承接列
-- （判据编码，M2-7 指标同源），卡面按 v5.29 复核订正，不重复建列。
--
-- 幂等：可重复执行。老行为 NULL（结果页不显示逐空明细，判分粒度=整题）。

ALTER TABLE exam_submission_items ADD COLUMN IF NOT EXISTS blank_results JSON;
ALTER TABLE exam_submission_items ADD COLUMN IF NOT EXISTS blanks JSON;

COMMENT ON COLUMN exam_submission_items.blank_results IS
    '填空题逐空判定明细 [{idx, student, expected, verdict}]（M1-1 卡面补列）；老数据 NULL';
COMMENT ON COLUMN exam_submission_items.blanks IS
    '学生逐空原始作答（M1-7 异步判分前置）；判分环节据 status=pending 行补判';
