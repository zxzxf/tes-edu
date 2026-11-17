-- 0025_tcap4_quarantine_queue.sql
-- T-CAP4 入库校验管线的「隔离原因落库」+ 异常队列页支撑。
-- 2026-11-17
--
-- 背景：v5.20 三条入库链路（采集 / 教师录题 / 批量导入）已统一走三闸，
--       quarantine 题落 status='quarantine' + source_tag='<base>:guard_quarantine'。
--       但「为什么被隔离」只存在于进程内存的 stats.samples 里，**没有落库**——
--       异常队列页拿不到 gate / reason，人工无从裁决，只能看到一条被挡住的题。
--
-- 本次补两列：
--   guard_gate    命中哪道闸（dedup_exact / dedup_text / answer_out_of_range /
--                missing_answer / mechanical_mismatch / dual_source_conflict /
--                choice_no_second_source / numeric_no_reference ...）
--   guard_reason  该次裁定的具体依据（含重复题的 hash 前缀、越界的选项字母等）
--
-- 语义约定：
--   * 只有 guard 真正做过分流（quarantine 或 manual 打标）的行才有值；
--     自动通过(pass)与未过闸的存量行保持 NULL。
--   * 人工处置后由 API 清空这两列（处置痕迹改由 reviewed_by/reviewed_at +
--     source_tag 的 guard_released / guard_archived 承载），避免「已处置但仍显示
--     隔离原因」的误导。
--
-- 幂等：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，可重复执行。
-- ⚠️ 须在 web-backend 新镜像发版前执行（新代码会读写这两列，缺列直接 500）。

BEGIN;

ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS guard_gate   VARCHAR(32);
ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS guard_reason TEXT;

-- 异常队列页的两个主查询：按 status 取隔离题、按 source_tag 取打标题。
-- 单列索引在 274 行上意义不大，但题库上万后就是全表扫，先建上不亏。
CREATE INDEX IF NOT EXISTS ix_course_kb_status      ON course_kb (status);
CREATE INDEX IF NOT EXISTS ix_course_kb_source_tag  ON course_kb (source_tag);

COMMENT ON COLUMN course_kb.guard_gate   IS 'T-CAP4 三闸命中项（异常队列展示用）';
COMMENT ON COLUMN course_kb.guard_reason IS 'T-CAP4 裁定依据，人工裁决时参考';

COMMIT;
