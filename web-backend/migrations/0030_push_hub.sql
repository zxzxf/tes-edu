-- 通用 App 推送中心（Push Hub）P1 内核建表（docx/通用App推送中心实施计划.md v1.4）
--
-- 九张表：
--   push_account        推送中心第三方账号（自助注册，独立身份域，不并入主站 RBAC）
--   push_account_role   账号角色（developer/operator/viewer，可限定 scope 应用）
--   push_app            接入应用（owner_account_id 归属第三方；NULL=平台自有）
--   push_token          推送 token（档位 1/3/6/12 个月；PG 只存 SHA-256 哈希）
--   push_topic          推送主题（注册制；渠道路由/频控/默认 TTL 挂主题）
--   push_channel        渠道配置（本期两种：wechat_oa / app_offline；凭证 Fernet 密文）
--   push_subscription   收件人按主题订阅/静音
--   push_message        消息主体（唯一事实源：离线不丢；msg_id unique 幂等）
--   push_delivery_log   投递留痕（每渠道每次 attempt 结果）
--
-- 类型说明：JSON 列沿用 SQLAlchemy generic JSON（PG 落 json、sqlite 测试库兼容，
-- 同 0029 决策）；主键一律 BIGSERIAL（sqlite 侧 INTEGER PRIMARY KEY AUTOINCREMENT）。
-- 幂等：可重复执行（IF NOT EXISTS）。

CREATE TABLE IF NOT EXISTS push_account (
    account_id   BIGSERIAL PRIMARY KEY,
    email        VARCHAR(255) NOT NULL UNIQUE,
    phone        VARCHAR(32),
    password_hash VARCHAR(255) NOT NULL,          -- bcrypt，禁明文
    org_name     VARCHAR(128) NOT NULL,
    contact      VARCHAR(128),
    status       VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/active/suspended
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at  TIMESTAMPTZ,
    approved_by  VARCHAR(64),                      -- admin user_id 审批留痕
    reject_reason VARCHAR(512)
);
COMMENT ON TABLE push_account IS '推送中心第三方账号（独立身份域，与 sys_user 无交集）';

CREATE TABLE IF NOT EXISTS push_account_role (
    id           BIGSERIAL PRIMARY KEY,
    account_id   BIGINT NOT NULL REFERENCES push_account(account_id),
    role         VARCHAR(16) NOT NULL,             -- developer/operator/viewer
    scope_app_id BIGINT,                           -- 限定只管某应用；NULL=名下全部
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_push_role_scope UNIQUE (account_id, role, scope_app_id)
);
CREATE INDEX IF NOT EXISTS ix_push_role_account ON push_account_role(account_id);

CREATE TABLE IF NOT EXISTS push_app (
    app_id           BIGSERIAL PRIMARY KEY,
    owner_account_id BIGINT REFERENCES push_account(account_id),  -- NULL=平台自有
    name             VARCHAR(128) NOT NULL,
    allowed_channels JSON NOT NULL DEFAULT '["app_offline"]',
    rate_limit_per_min INT NOT NULL DEFAULT 60,
    ip_allow         VARCHAR(512),                 -- 逗号分隔 CIDR；空=不限
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_push_app_owner ON push_app(owner_account_id);

CREATE TABLE IF NOT EXISTS push_token (
    token_id         BIGSERIAL PRIMARY KEY,
    app_id           BIGINT NOT NULL REFERENCES push_app(app_id),
    token_hash       CHAR(64) NOT NULL UNIQUE,     -- SHA-256 hex，明文只回显一次
    token_prefix     VARCHAR(12) NOT NULL,         -- pth-ab12 展示定位用
    tier             SMALLINT NOT NULL,            -- 档位月数：1/3/6/12
    issued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expired_at       TIMESTAMPTZ NOT NULL,
    grace_until      TIMESTAMPTZ,                  -- 续期后旧 token 宽限截止
    status           VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/rotated/revoked
    rate_limit_per_min INT,                        -- 可在 token 上单独收紧；NULL=随应用
    last_used_at     TIMESTAMPTZ,
    created_by       VARCHAR(64)                   -- 签发者（portal account_id 或 admin user_id）
);
CREATE INDEX IF NOT EXISTS ix_push_token_app ON push_token(app_id);
COMMENT ON COLUMN push_token.grace_until IS '续期=换发：被替换的旧 token 记 grace_until=新 token 签发+7天，期内并行可用，之后立即失效';

CREATE TABLE IF NOT EXISTS push_topic (
    topic_id         BIGSERIAL PRIMARY KEY,
    app_id           BIGINT NOT NULL REFERENCES push_app(app_id),
    topic_code       VARCHAR(64) NOT NULL,
    name             VARCHAR(128) NOT NULL,
    default_channels JSON NOT NULL DEFAULT '["app_offline"]',  -- 主题级渠道路由
    default_ttl      INT,                                    -- 秒；NULL=应用/全局默认
    rate_limit_per_day INT NOT NULL DEFAULT 10000,           -- 主题日频控（省微信额度）
    force_channels   JSON NOT NULL DEFAULT '[]',             -- 无视静音强制触达的渠道
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_push_topic_code UNIQUE (app_id, topic_code)
);

CREATE TABLE IF NOT EXISTS push_channel (
    channel_id        BIGSERIAL PRIMARY KEY,
    channel_type      VARCHAR(32) NOT NULL UNIQUE,   -- wechat_oa / app_offline（vendor/webhook 预留）
    name              VARCHAR(128) NOT NULL,
    config            JSON,                          -- 凭证 Fernet 密文串 gpt:...（禁明文）
    cred_valid_until  TIMESTAMPTZ,                   -- 渠道凭证有效期（到期页内提醒）
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_per_min INT NOT NULL DEFAULT 60,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS push_subscription (
    id             BIGSERIAL PRIMARY KEY,
    app_id         BIGINT NOT NULL REFERENCES push_app(app_id),
    receiver       VARCHAR(64) NOT NULL,             -- 学生用户 ID / alias / openid
    topic_code     VARCHAR(64) NOT NULL,
    muted          BOOLEAN NOT NULL DEFAULT FALSE,   -- 静音：app_offline 不计数仍落库可查
    channel_override JSON,                            -- 个人改此主题触达渠道（可选）
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_push_sub UNIQUE (app_id, receiver, topic_code)
);

CREATE TABLE IF NOT EXISTS push_message (
    msg_id         VARCHAR(36) PRIMARY KEY,           -- uuid4，Kafka at-least-one 幂等锚点
    app_id         BIGINT NOT NULL REFERENCES push_app(app_id),
    token_id       BIGINT REFERENCES push_token(token_id),  -- 留痕谁发的
    topic_code     VARCHAR(64) NOT NULL,
    receiver       VARCHAR(64) NOT NULL,
    title          VARCHAR(128) NOT NULL,
    body           TEXT NOT NULL,
    payload        JSON,
    channel_types  JSON,                               -- NULL/[] = 按主题路由→退应用默认
    expire_at      TIMESTAMPTZ,                        -- NULL=不过期（默认仍给全局 TTL）
    status         VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/sent/partial/expired/dead
    read_at        TIMESTAMPTZ,                        -- 收件箱已读时间；NULL=未读（计数/角标依据）
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_push_msg_recv ON push_message(receiver, topic_code, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_push_msg_app  ON push_message(app_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_push_msg_unread ON push_message(receiver, read_at) WHERE read_at IS NULL;

CREATE TABLE IF NOT EXISTS push_delivery_log (
    id           BIGSERIAL PRIMARY KEY,
    msg_id       VARCHAR(36) NOT NULL REFERENCES push_message(msg_id),
    channel_type VARCHAR(32) NOT NULL,
    attempt      SMALLINT NOT NULL DEFAULT 1,
    result_code  VARCHAR(32) NOT NULL,                -- ok / provider_code / timeout / skipped_muted / expired
    detail       VARCHAR(512),
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_push_deliv_msg ON push_delivery_log(msg_id);
