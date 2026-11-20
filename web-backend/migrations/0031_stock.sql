-- 股票盯盘业务中心（docx/股票盯盘业务中心实施计划.md v1.4）建表
--
-- 七张表：
--   stock_watch_group   自选股分组
--   stock_watchlist     自选股（symbol unique，置顶+分组）
--   stock_alert         预警规则（阈值+推送开关+接收方令牌+冷却）
--   stock_alert_log     触发记录（每个接收方推送 msg_id 留痕）
--   stock_friend        PushPlus 好友令牌（别名+32hex 令牌，敏感不进日志）
--   stock_config        单 KV 配置（push_default 全局默认接收方）
--   （行情历史不落 PG：Redis 快照 TTL 1 天 + K线现拉源缓存，自用够用；
--     provider/kline 由 app/services/stock/ 实现，见计划书 2.1）
--
-- 类型口径同 0030：JSON 列 generic JSON（PG 落 json）；BIGSERIAL 主键；
-- 可重复执行（IF NOT EXISTS）。

CREATE TABLE IF NOT EXISTS stock_watch_group (
    group_id   BIGSERIAL PRIMARY KEY,
    name       VARCHAR(64) NOT NULL UNIQUE,
    sort       INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock_watchlist (
    id         BIGSERIAL PRIMARY KEY,
    symbol     VARCHAR(16) NOT NULL,
    name       VARCHAR(64) NOT NULL DEFAULT '',
    group_id   BIGINT REFERENCES stock_watch_group(group_id),
    pinned     BOOLEAN NOT NULL DEFAULT FALSE,
    sort       INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_stock_watch_symbol UNIQUE (symbol)
);
CREATE INDEX IF NOT EXISTS ix_stock_watch_group ON stock_watchlist(group_id);

CREATE TABLE IF NOT EXISTS stock_friend (
    friend_id  BIGSERIAL PRIMARY KEY,
    alias      VARCHAR(64) NOT NULL,
    token      VARCHAR(64) NOT NULL UNIQUE,   -- PushPlus 好友令牌（敏感，禁日志）
    note       VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE stock_friend IS 'PushPlus 推送好友（对方扫渠道账号好友二维码后在好友列表获得令牌）';

CREATE TABLE IF NOT EXISTS stock_alert (
    alert_id       BIGSERIAL PRIMARY KEY,
    symbol         VARCHAR(16) NOT NULL,
    name           VARCHAR(64) NOT NULL DEFAULT '',
    condition      VARCHAR(16) NOT NULL,      -- price_above/price_below/pct_up/pct_down
    threshold      NUMERIC(14,4) NOT NULL,
    push_enabled   BOOLEAN NOT NULL DEFAULT TRUE,   -- 关=只落库+站内提示
    receiver_tokens JSON NOT NULL DEFAULT '[]',     -- 好友令牌；空=跟随全局默认
    cooldown_minutes INT NOT NULL DEFAULT 30,       -- 同规则冷却（PushPlus 限频防封号）
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN stock_alert.receiver_tokens IS '空数组=跟随 stock_config.push_default；默认亦 SELF=推自己';

CREATE TABLE IF NOT EXISTS stock_alert_log (
    id        BIGSERIAL PRIMARY KEY,
    alert_id  BIGINT NOT NULL REFERENCES stock_alert(alert_id),
    symbol    VARCHAR(16) NOT NULL,
    price     NUMERIC(14,4) NOT NULL,
    pct       NUMERIC(8,4) NOT NULL DEFAULT 0,
    fired_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    pushed    BOOLEAN NOT NULL DEFAULT FALSE,
    receivers JSON NOT NULL DEFAULT '[]',     -- [{receiver,msg_id,status}]
    detail    VARCHAR(512)
);
CREATE INDEX IF NOT EXISTS ix_stock_log_alert_time ON stock_alert_log(alert_id, fired_at);

CREATE TABLE IF NOT EXISTS stock_config (
    key        VARCHAR(64) PRIMARY KEY,
    value      JSON NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN stock_config.value IS 'key=push_default 时 {"mode":"self"|"friends","tokens":[好友令牌]}';
