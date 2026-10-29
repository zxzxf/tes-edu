-- 0005: course_kb 补 tenant_id + source_tag
-- 判分 / 组卷跨租户隔离前置（方案书 v1.2/v1.3：course_kb 无 tenant_id 硬缺口）。
-- 必须先于 import_bank 导入执行；幂等（ADD COLUMN IF NOT EXISTS）。

ALTER TABLE course_kb
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(32) NOT NULL DEFAULT 'school_pilot_01';

ALTER TABLE course_kb
    ADD COLUMN IF NOT EXISTS source_tag TEXT NOT NULL DEFAULT 'gaokao_real';

-- 旧 211 行（高考真题）tenant 由 DEFAULT 归 school_pilot_01；
-- source_tag 默认为 gaokao_real，与既有数据一致。
-- 新导入题经 import_bank 显式写入 source_tag='import'（覆盖默认值）。
