-- 0022_compliance_pipeline.sql
-- 合规审核队列接真实生产者（2026-11-14）。
--
-- 背景：compliance_items 自 2026-10-23 由内存占位改成持久化后，生产库一直是 0 行——
--       全仓库只有手工 POST /compliance 一个写入口，前端也没有送审按钮，
--       网关内容安全命中只打日志、不入队。设计方案里的 audit_queue 从未落地。
--
-- 本迁移做三件事：
--   1. 补租户与溯源列（tenant_id / ref_type / ref_id / dedup_key / source / 审核留痕）；
--   2. 建幂等键：同一道题、同一天同一路径同一敏感词的网关拦截只入队一次；
--   3. 把**存量真实数据**（course_kb.status='draft' 的题目）回填为待审项，
--      让合规审核页立刻有内容可审，而不是塞演示假数据。
--
-- 幂等：ADD COLUMN IF NOT EXISTS / 唯一索引 / ON CONFLICT DO NOTHING，可重复执行。
-- 回滚：compliance_items_bak_20261114 保存加列前的原表（当前为空表）。
--
-- ⚠️ 必须在 web-backend 新镜像发版**之前**执行：新代码写侧会引用这些列。
-- 执行方式见 deploy README；线上库为 PostgreSQL，不要用 SQLite 语法。

BEGIN;

-- 加列前快照（只建一次）
CREATE TABLE IF NOT EXISTS compliance_items_bak_20261114 AS
SELECT * FROM compliance_items;

ALTER TABLE compliance_items
    ADD COLUMN IF NOT EXISTS tenant_id    VARCHAR(64),
    ADD COLUMN IF NOT EXISTS ref_type     VARCHAR(24),
    ADD COLUMN IF NOT EXISTS ref_id       INTEGER,
    ADD COLUMN IF NOT EXISTS dedup_key    VARCHAR(160),
    ADD COLUMN IF NOT EXISTS source       VARCHAR(24),
    ADD COLUMN IF NOT EXISTS reviewed_by  VARCHAR(64),
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

-- 历史行（若存在）挂到试点校，避免成为谁都看不到的孤儿行
UPDATE compliance_items
SET tenant_id = 'school_pilot_01',
    ref_type  = COALESCE(ref_type, 'manual'),
    source    = COALESCE(source, 'manual')
WHERE tenant_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_compliance_tenant_status
    ON compliance_items (tenant_id, status);

-- 部分唯一索引：dedup_key 为空的历史行不参与去重
CREATE UNIQUE INDEX IF NOT EXISTS uq_compliance_dedup
    ON compliance_items (tenant_id, dedup_key)
    WHERE dedup_key IS NOT NULL;

-- 存量 draft 题目 → 待审项。只认有租户、有题干的行；ON CONFLICT 保证重跑不重复。
INSERT INTO compliance_items
    (tenant_id, type, content, status, ref_type, ref_id, dedup_key, source, created_at)
SELECT q.tenant_id,
       '题目',
       q.stem,
       'pending',
       'question',
       q.id,
       'question:' || q.id,
       CASE WHEN q.source_tag = 'auto_crawl' THEN 'crawl' ELSE 'question' END,
       COALESCE(q.created_at, now())
FROM course_kb q
WHERE q.status = 'draft'
  AND q.tenant_id IS NOT NULL
  AND q.stem IS NOT NULL
ON CONFLICT (tenant_id, dedup_key) WHERE dedup_key IS NOT NULL DO NOTHING;

COMMENT ON COLUMN compliance_items.dedup_key IS
    '幂等键：question:<题库id> / gateway:<method>:<path>:<keyword>:<日期>；为空的历史行不去重';
COMMENT ON COLUMN compliance_items.ref_type IS
    '来源对象：question=录题 / crawl=采集入库 / gateway_reject=网关敏感词拦截 / manual=手工';

COMMIT;
