-- M0-4 RAG 检索表补 tenant_id（2026-11-03）
--
-- 背景：`course_kb` 已有 tenant_id，但 agent-backend 真正检索的向量表
-- `course_kb_embeddings` 没有租户列，`rag._search_pg` 也不带租户条件。
-- 后果：别校题目会被当成本校本题的「标答」召回，进而参与判分——
-- 这是比教师端列表串题更严重的跨校数据泄漏。
--
-- 旧数据归属：与 M0-4 对 `course_kb` 的处理一致，全部归试点校
-- `school_pilot_01`（§6-13 定案）。import_bank / sync_embeddings 会按
-- `course_kb.tenant_id` 回填真实归属。
--
-- 幂等：ADD COLUMN IF NOT EXISTS + UPDATE 仅改默认值行，可重复执行。

ALTER TABLE course_kb_embeddings
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(32) NOT NULL DEFAULT 'school_pilot_01';

-- 存量行统一归试点校（DEFAULT 已覆盖新增行，这里显式回填以防列曾以可空方式添加）
UPDATE course_kb_embeddings
   SET tenant_id = 'school_pilot_01'
 WHERE tenant_id IS NULL OR tenant_id = '';

COMMENT ON COLUMN course_kb_embeddings.tenant_id IS '租户隔离键；RAG 检索必须按此过滤（M0-4）';

-- 检索主形态：WHERE tenant_id = $ AND subject = $ ORDER BY embedding <=> $
CREATE INDEX IF NOT EXISTS ix_kb_embeddings_tenant_subject
    ON course_kb_embeddings (tenant_id, subject);