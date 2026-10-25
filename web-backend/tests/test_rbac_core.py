"""T-AM1/T-AM2 权限基座纯逻辑单测（不连库）。

覆盖：
- Role 枚举扩展（REGION_ADMIN 存在、RESEARCHER 废弃）
- 种子结构（7区域/5角色/菜单树含按钮码/角色-菜单授权）
- 权限聚合 build_permission_set
- PermissionEngine.has_perm（含 *:* 与通配前缀）
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import permission as perm_mod
from app.core.rbac import Role, ROLE_MODULES, ROLE_PERMISSIONS
from app.services import rbac_seed


def test_role_enum_extended():
    assert Role.REGION_ADMIN.value == "region_admin"
    # 废弃 researcher
    codes = {r.value for r in Role}
    assert "researcher" not in codes
    assert Role.STUDENT.value == "student"


def test_role_modules_backward_compat():
    # ROLE_MODULES 保留作兼容（require_role 粗粒度仍可用）
    assert ROLE_MODULES[Role.ADMIN] == "*"
    assert "reports" in ROLE_MODULES[Role.PARENT]


def test_seed_regions_seven():
    regions = rbac_seed.build_seed_regions()
    assert len(regions) == 7
    codes = [r["region_code"] for r in regions]
    assert codes == ["east", "north", "central", "south", "southwest", "northwest", "northeast"]


def test_seed_roles_five():
    roles = rbac_seed.build_seed_roles()
    assert len(roles) == 5
    codes = {r["role_code"] for r in roles}
    assert codes == {"admin", "region_admin", "school", "teacher", "parent"}


def test_seed_menus_have_button_codes():
    menus = rbac_seed.build_seed_menus()
    buttons = [m for m in menus if m["menu_type"] == "BUTTON"]
    assert buttons, "应有按钮级节点"
    codes = {m["permission_code"] for m in buttons}
    assert "system:menu:add" in codes
    assert "system:role:auth" in codes
    # 目录/菜单 permission_code 为空
    dirs = [m for m in menus if m["menu_type"] == "DIR"]
    assert all(m["permission_code"] is None for m in dirs)


def test_seed_role_menus_admin_full():
    rm = rbac_seed.build_seed_role_menus()
    admin_menus = [x["menu_id"] for x in rm if x["role_id"] == "role_admin"]
    # admin 拥有全部菜单（目录/菜单/按钮）
    menu_ids = [m[0] for m in rbac_seed.MENUS]
    assert set(admin_menus) == set(menu_ids)


def test_seed_role_menus_region_admin_no_system_settings():
    rm = rbac_seed.build_seed_role_menus()
    ra_menus = {x["menu_id"] for x in rm if x["role_id"] == "role_region_admin"}
    # region_admin 看不到系统设置（m_sys 及其子节点）
    assert "m_sys" not in ra_menus
    assert "m_sys_menu" not in ra_menus
    # 但可见区域运营
    assert "m_ops" in ra_menus
    assert "m_ops_tenant" in ra_menus


def test_build_permission_set_filters_none():
    rows = [
        {"permission_code": "system:menu:add"},
        {"permission_code": None},
        {"permission_code": "system:role:auth"},
    ]
    perms = perm_mod.build_permission_set(rows)
    assert perms == {"system:menu:add", "system:role:auth"}


def test_has_perm_wildcard_admin():
    eng = perm_mod.PermissionEngine()
    assert eng.has_perm({"*:*"}, "system:menu:delete") is True
    assert eng.has_perm({"*:*"}, "anything:here") is True


def test_has_perm_exact_and_prefix():
    eng = perm_mod.PermissionEngine()
    perms = {"system:menu:add", "system:role:*"}
    assert eng.has_perm(perms, "system:menu:add") is True
    # 通配前缀匹配
    assert eng.has_perm(perms, "system:role:auth") is True
    # 无权限
    assert eng.has_perm(perms, "system:vendor:manage") is False


def test_no_perm_returns_false():
    eng = perm_mod.PermissionEngine()
    assert eng.has_perm(set(), "system:menu:add") is False
