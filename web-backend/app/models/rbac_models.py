"""后台权限基座 ORM 模型（T-AM0/T-AM1）。

8 张权限表 + 租户表，与 course_kb 共用同一个 Base，启动时一次 create_all 全部建出：
- sys_tenant        租户/学校（T-AM0）
- sys_user          后台用户（替换 auth.py 的 _FAKE_USERS）
- sys_role          角色（admin/region_admin/school/teacher/parent）
- sys_user_role     用户-角色（一个用户在某租户下可有多角色）
- sys_menu          菜单树（DIR/MENU/BUTTON 三类；BUTTON 带 permission_code）
- sys_role_menu     角色-菜单授权（含其下按钮）
- sys_region        区域（全国七大区）
- sys_service_vendor  系统服务厂商（MCP 厂商注册表，T-AM6）
- mcp_service_instance 每厂商独立数据服务实例（T-AM6）
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)

from app.models.course_kb import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SysTenant(Base):
    """租户/学校（T-AM0）。region_admin 仅见本区域学校、school 仅见本校。"""

    __tablename__ = "sys_tenant"

    tenant_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    region_id = Column(String(64), index=True)  # 绑定区域
    status = Column(SmallInteger, default=1)  # 1 启用 / 0 停用
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class SysUser(Base):
    """后台用户（替换 auth.py 的 _FAKE_USERS）。web-backend 作为后台用户权威库。"""

    __tablename__ = "sys_user"

    user_id = Column(String(64), primary_key=True)
    username = Column(String(64), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)  # bcrypt，禁明文
    tenant_id = Column(String(64), index=True)  # 平台角色可空
    region_id = Column(String(64), index=True)  # 区域管理员/平台角色按此过滤
    status = Column(SmallInteger, default=1)  # 1 启用 / 0 禁用
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class SysRole(Base):
    """角色（admin/region_admin/school/teacher/parent；student 仅 App 端不进 Web 菜单）。"""

    __tablename__ = "sys_role"

    role_id = Column(String(64), primary_key=True)
    role_code = Column(String(32), nullable=False, unique=True)
    role_name = Column(String(64), nullable=False)
    tenant_id = Column(String(64), index=True)  # 租户级角色（如某校 teacher）
    region_id = Column(String(64), index=True)  # 区域管理员绑定所辖区域
    remark = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class SysUserRole(Base):
    """用户-角色（一个用户在某租户下可有多角色）。"""

    __tablename__ = "sys_user_role"

    user_id = Column(String(64), nullable=False, primary_key=True)
    role_id = Column(String(64), nullable=False, primary_key=True)
    tenant_id = Column(String(64), primary_key=True)
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "tenant_id", name="uq_user_role_tenant"),
    )


class SysMenu(Base):
    """菜单树（DIR/MENU/BUTTON 三类；BUTTON 节点带 permission_code）。"""

    __tablename__ = "sys_menu"

    menu_id = Column(String(64), primary_key=True)
    parent_id = Column(String(64), index=True)  # 根节点为空
    menu_name = Column(String(64), nullable=False)
    menu_type = Column(String(8), nullable=False)  # DIR / MENU / BUTTON
    path = Column(String(128))
    component = Column(String(128))
    icon = Column(String(64))
    sort = Column(Integer, default=0)
    permission_code = Column(String(64), index=True)  # 按钮级权限码
    visible = Column(Boolean, default=True)
    status = Column(SmallInteger, default=1)
    tenant_id = Column(String(64), index=True)  # 平台菜单空；租户可定制时填


class SysRoleMenu(Base):
    """角色-菜单授权（含其下按钮权限）。"""

    __tablename__ = "sys_role_menu"

    role_id = Column(String(64), nullable=False, primary_key=True)
    menu_id = Column(String(64), nullable=False, primary_key=True)
    __table_args__ = (
        UniqueConstraint("role_id", "menu_id", name="uq_role_menu"),
    )


class SysRegion(Base):
    """区域（全国七大区：华东/华北/华中/华南/西南/西北/东北）。"""

    __tablename__ = "sys_region"

    region_id = Column(String(64), primary_key=True)
    region_code = Column(String(32), nullable=False, unique=True)
    region_name = Column(String(64), nullable=False)
    parent_id = Column(String(64), index=True)
    sort = Column(Integer, default=0)


class SysServiceVendor(Base):
    """系统服务厂商（MCP 厂商注册表，T-AM6）。"""

    __tablename__ = "sys_service_vendor"

    vendor_id = Column(String(64), primary_key=True)
    vendor_name = Column(String(128), nullable=False)
    vendor_type = Column(String(32), nullable=False)  # aliyun/tencent/ark/deepseek/custom...
    base_url = Column(Text)
    auth_type = Column(String(32))  # apikey/oauth/aksk
    credential_enc = Column(LargeBinary)  # AES-256-GCM 加密凭证
    status = Column(SmallInteger, default=1)
    region_id = Column(String(64), index=True)
    tenant_id = Column(String(64), index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class McpServiceInstance(Base):
    """每厂商独立数据服务实例（互不干扰的核心，T-AM6）。"""

    __tablename__ = "mcp_service_instance"

    instance_id = Column(String(64), primary_key=True)
    vendor_id = Column(String(64), nullable=False, index=True)
    service_name = Column(String(128), nullable=False)
    data_namespace = Column(String(128), nullable=False)  # schema / keyspace / 存储前缀
    connection_enc = Column(LargeBinary)  # 每实例独立连接/凭证（再加密一层）
    isolation_mode = Column(String(16), default="logical")  # logical / physical
    status = Column(SmallInteger, default=1)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
