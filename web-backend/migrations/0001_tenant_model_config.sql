-- BYOM T-M1: 租户级模型配置表
-- 密钥以 AES-256-GCM 密文（nonce12 || ct || tag16）存于 api_key_enc / embed_api_key_enc，
-- 明文绝不落库、绝不随 API 响应出库（与主密钥 VENDOR_CREDENTIAL_KEY 同源，见 app/services/vendor_crypto.py）。
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS tenant_model_config (
    tenant_id          VARCHAR(64)   NOT NULL,
    provider           VARCHAR(32)   NOT NULL DEFAULT 'custom',
    base_url           TEXT          NOT NULL,
    api_key_enc        BYTEA         NOT NULL,
    model              VARCHAR(128)  NOT NULL,
    embed_base_url     TEXT,
    embed_api_key_enc  BYTEA,
    embed_model        VARCHAR(128),
    embed_dim          INTEGER       DEFAULT 1024,
    model_simple       VARCHAR(128),
    model_hard         VARCHAR(128),
    enabled            BOOLEAN       DEFAULT TRUE,
    rag_top_k          INTEGER       DEFAULT 4,
    created_at         TIMESTAMPTZ   DEFAULT now(),
    updated_at         TIMESTAMPTZ   DEFAULT now(),
    PRIMARY KEY (tenant_id)
);

-- 注释（Postgres）：api_key_enc 为密文 BLOB，长度 = 12(nonce) + N + 16(tag)。
COMMENT ON TABLE  tenant_model_config        IS 'BYOM 租户级模型配置（密钥密文存储）';
COMMENT ON COLUMN tenant_model_config.api_key_enc IS 'AES-256-GCM 密文（nonce12||ct||tag16），明文不入库';
