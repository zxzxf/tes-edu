"""股票盯盘业务中心（docx/股票盯盘业务中心实施计划.md v1.4）ORM 模型。

七张表与 migrations/0031_stock.sql 一一对应；共用 course_kb 的 Base，
启动 create_all 幂等建出（sqlite 单测同库建表，JSON 列 generic JSON）。

口径：
- symbol 一律小写带市场前缀（sh600519/sz000858），指数用 s_sh000001 形态；
- 好友令牌（PushPlus 32hex）落库但属敏感配置：接口响应可回显（自用 admin 单用户），
  日志一律不落；
- 推送经平台推送中心（wechat_oa 渠道）：receiver='SELF' 推渠道账号本人，
  receiver=好友令牌走 to 转发——push_message.receiver 是 String(64)，
  多好友必须一人一条 emit（alert_log.msg_ids 逐条记录）。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StockWatchGroup(Base):
    """自选股分组（自用简单版：仅名称，全局共享不分用户）。"""

    __tablename__ = "stock_watch_group"

    group_id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False, unique=True)
    sort = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class StockWatchlist(Base):
    """自选股：symbol 唯一，置顶 + 分组 + 组内排序。"""

    __tablename__ = "stock_watchlist"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_stock_watch_symbol"),
        Index("ix_stock_watch_group", "group_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False)  # sh600519 小写
    name = Column(String(64), nullable=False, default="")
    group_id = Column(Integer, ForeignKey("stock_watch_group.group_id"))
    pinned = Column(Boolean, nullable=False, default=False)
    sort = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class StockFriend(Base):
    """推送好友：PushPlus 好友令牌（对方扫 admin 的好友二维码后在好友列表获得）。"""

    __tablename__ = "stock_friend"

    friend_id = Column(Integer, primary_key=True, autoincrement=True)
    alias = Column(String(64), nullable=False)
    token = Column(String(64), nullable=False, unique=True)  # 32hex，敏感：不进日志
    note = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class StockAlert(Base):
    """涨跌预警规则。

    condition：price_above/price_below（绝对价）、pct_up/pct_down（涨跌幅百分比）。
    receiver_tokens JSON 列表：空=跟随全局默认（stock_config.push_default）；
    全局默认也是 SELF=推自己。cooldown_minutes 内同规则只触发一次（防刷屏限频）。
    """

    __tablename__ = "stock_alert"

    alert_id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False)
    name = Column(String(64), nullable=False, default="")
    condition = Column(String(16), nullable=False)  # price_above/price_below/pct_up/pct_down
    threshold = Column(Numeric(14, 4), nullable=False)
    push_enabled = Column(Boolean, nullable=False, default=True)  # 关=只落日志不推微信
    receiver_tokens = Column(JSON, nullable=False, default=list)  # 好友令牌；空=跟随默认
    cooldown_minutes = Column(Integer, nullable=False, default=30)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class StockAlertLog(Base):
    """预警触发记录（站内提示数据源）：含每个接收方的推送 msg_id 与结果。

    receivers JSON：[{"receiver": "SELF"|好友令牌, "msg_id": uuid, "status": "sent|dead|..."}]
    推送经推送中心异步分发，落库时 status=dispatched，前端可经 msg_id 查终态。
    """

    __tablename__ = "stock_alert_log"
    __table_args__ = (
        Index("ix_stock_log_alert_time", "alert_id", "fired_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(Integer, ForeignKey("stock_alert.alert_id"), nullable=False)
    symbol = Column(String(16), nullable=False)
    price = Column(Numeric(14, 4), nullable=False)
    pct = Column(Numeric(8, 4), nullable=False, default=0)
    fired_at = Column(DateTime(timezone=True), default=_utcnow)
    pushed = Column(Boolean, nullable=False, default=False)  # 本次是否实际推了微信
    receivers = Column(JSON, nullable=False, default=list)
    detail = Column(String(512))


class StockConfig(Base):
    """单 KV 配置表（自用）：key='push_default' → value={"mode":"self"|"friends","tokens":[...]}。"""

    __tablename__ = "stock_config"

    key = Column(String(64), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
