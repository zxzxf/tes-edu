-- T-D3：student_events 增加 degraded 列（主解/Hint 由模板回落时置 true）。
-- 幂等：可重复执行。
ALTER TABLE student_events ADD COLUMN IF NOT EXISTS degraded BOOLEAN;

-- 降级事件检索（按时间/事件类型聚合降级率）
CREATE INDEX IF NOT EXISTS idx_student_events_degraded
    ON student_events (degraded, occurred_at);
