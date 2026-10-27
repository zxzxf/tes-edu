-- W6 BYOM T-M3：预设供应商清单（可热更新配置表）
-- 与 web-backend/app/models/byom_preset.py 对齐；种子数据由 app/services/byom_preset.py
-- 的 seed_presets 在启动时幂等写入（provider 已存在则跳过），故此处只建表。
CREATE TABLE IF NOT EXISTS vendor_preset (
    provider        VARCHAR(32)  NOT NULL,
    display_name    VARCHAR(64)  NOT NULL,
    base_url        TEXT         NOT NULL,
    models          TEXT,                       -- 逗号分隔候选模型列表
    embed_base_url  TEXT,                       -- 缺省复用 base_url
    embed_model     VARCHAR(128),
    notes           TEXT,
    sort_order      INT          DEFAULT 0,
    enabled         BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT now(),
    PRIMARY KEY (provider)
);
