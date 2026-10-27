-- BYOM T-VLM: 租户级视觉模型（拍照识题）配置段
-- 复用 chat 段的 api_key（即 LLM_API_KEY），不再单独存密钥列；
-- 仅新增 vlm_base_url（缺省复用 chat base_url）与 vlm_model（视觉模型名）。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行。

ALTER TABLE tenant_model_config
    ADD COLUMN IF NOT EXISTS vlm_base_url TEXT;

ALTER TABLE tenant_model_config
    ADD COLUMN IF NOT EXISTS vlm_model VARCHAR(128);

COMMENT ON COLUMN tenant_model_config.vlm_base_url IS '视觉模型端点；留空复用 chat base_url';
COMMENT ON COLUMN tenant_model_config.vlm_model IS '视觉模型名（如 qwen-vl-max / gpt-4o）；留空则本租户拍照识题回落全局 VLM';
