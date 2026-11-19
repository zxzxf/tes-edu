"""通用 App 推送中心（Push Hub）ORM 模型（P1 内核）。

九张表与 migrations/0030_push_hub.sql 一一对应；共用 course_kb 的 Base，
启动 create_all 幂等建出（sqlite 单测同库建表，JSON 列用 generic JSON，
PG 落 json——类型口径同 0029 决策）。

身份域隔离（v1.4 设计底线）：
- push_account 独立于 sys_user——注册/登录/会话全走 app/core/push_security.py，
  会话令牌与主站 JWT 不同密钥、不同受众，互不互认；
- 第三方 portal 接口权限闭包=推送中心域内路由，新增接口默认不进白名单。
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PushAccount(Base):
    """推送中心第三方账号（自助注册，status 门禁：pending 一切写接口拒绝）。"""

    __tablename__ = "push_account"

    account_id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(32))
    password_hash = Column(String(255), nullable=False)  # bcrypt，禁明文
    org_name = Column(String(128), nullable=False)
    contact = Column(String(128))
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    approved_at = Column(DateTime(timezone=True))
    approved_by = Column(String(64))
    reject_reason = Column(String(512))


class PushAccountRole(Base):
    """账号角色：developer（应用/token/主题/send）/ operator（主题+查询+重试）/ viewer（只读）。"""

    __tablename__ = "push_account_role"
    __table_args__ = (
        UniqueConstraint("account_id", "role", "scope_app_id", name="uq_push_role_scope"),
        Index("ix_push_role_account", "account_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("push_account.account_id"), nullable=False)
    role = Column(String(16), nullable=False)
    scope_app_id = Column(Integer)  # 限定只管某应用；NULL=名下全部


class PushApp(Base):
    """接入应用。owner_account_id=NULL 为平台自有（student app 等），主站 admin 维护。"""

    __tablename__ = "push_app"
    __table_args__ = (Index("ix_push_app_owner", "owner_account_id"),)

    app_id = Column(Integer, primary_key=True, autoincrement=True)
    owner_account_id = Column(Integer, ForeignKey("push_account.account_id"))
    name = Column(String(128), nullable=False)
    allowed_channels = Column(JSON, nullable=False, default=lambda: ["app_offline"])
    rate_limit_per_min = Column(Integer, nullable=False, default=60)
    ip_allow = Column(String(512))  # 逗号分隔 CIDR；空=不限
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class PushToken(Base):
    """推送 token：pth-<32hex>；PG 只存 SHA-256；档位 tier=1/3/6/12（月）。

    生命周期：active →（过期自然失效，Redis TTL 对齐 expired_at）
    → 续期=换发：旧行 status=rotated + grace_until=新签发+7d 并行宽限
    → 吊销 revoked 即时 401；应用禁用/账号停号联动失效（校验时回溯查上层状态）。
    """

    __tablename__ = "push_token"
    __table_args__ = (Index("ix_push_token_app", "app_id"),)

    token_id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("push_app.app_id"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    token_prefix = Column(String(12), nullable=False)  # pth-ab12 展示定位
    tier = Column(SmallInteger, nullable=False)
    issued_at = Column(DateTime(timezone=True), default=_utcnow)
    expired_at = Column(DateTime(timezone=True), nullable=False)
    grace_until = Column(DateTime(timezone=True))
    status = Column(String(16), nullable=False, default="active")
    rate_limit_per_min = Column(Integer)  # 单独收紧；NULL=随应用
    last_used_at = Column(DateTime(timezone=True))
    created_by = Column(String(64))


class PushTopic(Base):
    """推送主题（应用内注册制）：渠道路由/日频控/默认 TTL/force_channels 挂这里。"""

    __tablename__ = "push_topic"
    __table_args__ = (
        UniqueConstraint("app_id", "topic_code", name="uq_push_topic_code"),
    )

    topic_id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("push_app.app_id"), nullable=False)
    topic_code = Column(String(64), nullable=False)
    name = Column(String(128), nullable=False)
    default_channels = Column(JSON, nullable=False, default=lambda: ["app_offline"])
    default_ttl = Column(Integer)  # 秒；NULL=应用/全局默认
    rate_limit_per_day = Column(Integer, nullable=False, default=10000)
    force_channels = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class PushChannel(Base):
    """渠道配置（管理员可视化）。config 存 Fernet 密文串，明文凭证绝不落库。"""

    __tablename__ = "push_channel"

    channel_id = Column(Integer, primary_key=True, autoincrement=True)
    channel_type = Column(String(32), nullable=False, unique=True)  # wechat_oa/app_offline
    name = Column(String(128), nullable=False)
    config = Column(JSON)  # {"enc": "gpt:..."}；解密需 PUSH_SECRET_KEY
    cred_valid_until = Column(DateTime(timezone=True))
    enabled = Column(Boolean, nullable=False, default=True)
    rate_limit_per_min = Column(Integer, nullable=False, default=60)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PushSubscription(Base):
    """收件人按主题订阅/静音——App「消息设置」开关的数据源。"""

    __tablename__ = "push_subscription"
    __table_args__ = (
        UniqueConstraint("app_id", "receiver", "topic_code", name="uq_push_sub"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("push_app.app_id"), nullable=False)
    receiver = Column(String(64), nullable=False)
    topic_code = Column(String(64), nullable=False)
    muted = Column(Boolean, nullable=False, default=False)
    channel_override = Column(JSON)  # 个人改此主题触达渠道（可选）
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class PushMessage(Base):
    """消息主体=唯一事实源（离线不丢、分页补收、状态）。

    msg_id 由 emit 生成 uuid4，作 Kafka at-least-one 重复消费的幂等锚点。
    """

    __tablename__ = "push_message"
    __table_args__ = (
        Index("ix_push_msg_recv", "receiver", "topic_code", "created_at"),
        Index("ix_push_msg_app", "app_id", "created_at"),
    )

    msg_id = Column(String(36), primary_key=True)  # uuid4
    app_id = Column(Integer, ForeignKey("push_app.app_id"), nullable=False)
    token_id = Column(Integer, ForeignKey("push_token.token_id"))  # 留痕谁发的
    topic_code = Column(String(64), nullable=False)
    receiver = Column(String(64), nullable=False)
    title = Column(String(128), nullable=False)
    body = Column(Text, nullable=False)
    payload = Column(JSON)
    channel_types = Column(JSON)  # NULL/[] = 按主题路由→退应用默认
    expire_at = Column(DateTime(timezone=True))
    status = Column(String(16), nullable=False, default="pending")  # pending/sent/partial/expired/dead
    read_at = Column(DateTime(timezone=True))  # 收件箱已读时间；NULL=未读（计数/角标依据）
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class PushDeliveryLog(Base):
    """投递留痕：每渠道每次 attempt 一行。隐私口径：admin 看第三方只给统计，不给正文。"""

    __tablename__ = "push_delivery_log"
    __table_args__ = (Index("ix_push_deliv_msg", "msg_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    msg_id = Column(String(36), ForeignKey("push_message.msg_id"), nullable=False)
    channel_type = Column(String(32), nullable=False)
    attempt = Column(SmallInteger, nullable=False, default=1)
    result_code = Column(String(32), nullable=False)  # ok/timeout/skipped_muted/expired/…
    detail = Column(String(512))
    sent_at = Column(DateTime(timezone=True), default=_utcnow)
