-- T-CAP4 v5.23：text_fp 持久化索引 + 真题重复组人工裁决留痕表
--
-- 背景：第一闸此前每次扫描（入库 hook / 异常队列 dup_of / 存量脚本）都要把全库
-- stem 拉回 Python 现算 sha1。274 题尚可，上万题后是 O(n) 传输 + O(n) CPU；
-- 落列后按 (tenant_id, text_fp) 等值查，重复检测变成索引查找。
-- text_fingerprint 算法在 app/core/intake_guard.py，存量回填跑
-- `python scripts/backfill_text_fp.py --apply`（迁移器只做 DDL，不跑 Python 算法）。

ALTER TABLE course_kb ADD COLUMN IF NOT EXISTS text_fp VARCHAR(40);

CREATE INDEX IF NOT EXISTS ix_course_kb_text_fp ON course_kb (text_fp);
CREATE INDEX IF NOT EXISTS ix_course_kb_tenant_text_fp ON course_kb (tenant_id, text_fp);

-- 一组重复题的人工裁决留痕。group_key 由文本指纹/内容哈希算出（fp:/hash: 前缀），
-- 与题目数据解耦：裁决「全留」时不改任何题目，只写这里表示此组已看过。
CREATE TABLE IF NOT EXISTS dup_group_decisions (
    group_key VARCHAR(80) PRIMARY KEY,
    decision VARCHAR(16) NOT NULL,
    keep_id INTEGER,
    member_ids JSONB,
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
