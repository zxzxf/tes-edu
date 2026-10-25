"""权限基座建表 + 种子数据（T-AM1）。

- init_rbac():幂等建表（随 course_kb create_all 一并建出，这里显式保底）。
- seed_rbac():写入 7 区域 + 5 角色 + 默认菜单树 + admin/region_admin 账号。
- 纯函数 build_seed_*() 不连库，便于单测断言结构。
"""

from datetime import datetime, timezone
from typing import List

# 七大区（D4：一次性铺满）
REGIONS = [
    ("east", "华东"), ("north", "华北"), ("central", "华中"),
    ("south", "华南"), ("southwest", "西南"), ("northwest", "西北"),
    ("northeast", "东北"),
]

# 五角色（D3：废弃 researcher；student 仅 App 端不进 Web 菜单）
ROLES = [
    ("role_admin", "admin", "超级管理员"),
    ("role_region_admin", "region_admin", "区域管理员"),
    ("role_school", "school", "学校"),
    ("role_teacher", "teacher", "教师"),
    ("role_parent", "parent", "家长"),
]

# 默认菜单树：系统设置(菜单/角色/区域/系统服务) + 区域运营(学校/教研/学情/错题/组卷/批改/周报/合规)
# 按钮节点带 permission_code；目录/菜单 permission_code 为空。
MENUS = [
    # (menu_id, parent_id, name, type, path, component, icon, sort, permission_code)
    ("m_sys", None, "系统设置", "DIR", "/system", "Layout", "setting", 1, None),
    ("m_sys_menu", "m_sys", "菜单管理", "MENU", "/system/menu", "Menu", "menu", 1, None),
    ("m_sys_menu_add", "m_sys_menu", "新建菜单", "BUTTON", "", "", "", 1, "system:menu:add"),
    ("m_sys_menu_edit", "m_sys_menu", "修改菜单", "BUTTON", "", "", "", 2, "system:menu:edit"),
    ("m_sys_menu_del", "m_sys_menu", "删除菜单", "BUTTON", "", "", "", 3, "system:menu:delete"),
    ("m_sys_role", "m_sys", "角色管理", "MENU", "/system/role", "Role", "role", 2, None),
    ("m_sys_role_auth", "m_sys_role", "角色授权", "BUTTON", "", "", "", 1, "system:role:auth"),
    ("m_sys_region", "m_sys", "区域管理", "MENU", "/system/region", "Region", "region", 3, None),
    ("m_sys_region_edit", "m_sys_region", "编辑区域", "BUTTON", "", "", "", 1, "system:region:edit"),
    ("m_sys_vendor", "m_sys", "系统服务(MCP厂商)", "MENU", "/system/vendor", "Vendor", "cloud", 4, None),
    ("m_sys_vendor_manage", "m_sys_vendor", "厂商管理", "BUTTON", "", "", "", 1, "system:vendor:manage"),

    ("m_ops", None, "区域运营", "DIR", "/ops", "Layout", "ops", 2, None),
    ("m_ops_tenant", "m_ops", "学校管理", "MENU", "/ops/tenant", "Tenant", "school", 1, None),
    ("m_ops_knowledge", "m_ops", "教研内容管理", "MENU", "/ops/knowledge", "Knowledge", "book", 2, None),
    ("m_ops_analytics", "m_ops", "学情看板", "MENU", "/ops/analytics", "Analytics", "chart", 3, None),
    ("m_ops_wrong", "m_ops", "错题本", "MENU", "/ops/wrong", "Wrong", "wrong", 4, None),
    ("m_ops_exam", "m_ops", "自动组卷", "MENU", "/ops/exam", "Exam", "exam", 5, None),
    ("m_ops_exam_gen", "m_ops_exam", "生成组卷", "BUTTON", "", "", "", 1, "exam:generate"),
    ("m_ops_grading", "m_ops", "教师批改", "MENU", "/ops/grading", "Grading", "grade", 6, None),
    ("m_ops_grading_submit", "m_ops_grading", "提交批改", "BUTTON", "", "", "", 1, "grading:submit"),
    ("m_ops_grading_review", "m_ops_grading", "评分批改", "BUTTON", "", "", "", 2, "grading:review"),
    ("m_ops_reports", "m_ops", "家长周报", "MENU", "/ops/reports", "Reports", "report", 7, None),
    ("m_ops_compliance", "m_ops", "合规审核", "MENU", "/ops/compliance", "Compliance", "shield", 8, None),
    # T-AM7：前端已有页面补入菜单树（此前仅有静态导航，DB 无节点 → 动态菜单渲染不出）。
    # 班级错因分布（S5 老师端）；在线练习（过程采集）。
    ("m_ops_errordist", "m_ops", "班级错因分布", "MENU", "/ops/error-distribution", "ErrorDistribution", "search", 9, None),
    ("m_ops_practice", "m_ops", "在线练习", "MENU", "/ops/practice", "Practice", "target", 10, None),
]

# 角色 × 菜单授权（菜单节点 + 其下按钮一并授权）：仅 admin 见系统设置全部。
# region_admin 见区域运营全部（不含系统设置）；其余角色按矩阵缩减。
# parent 仅家长周报（B 端家长是周报读者，不做教学操作——与原前端静态
# meta.role 的偏差属有意收口，以 DB 授权矩阵为唯一权威）。
# 按钮级授权（T-AM7 起有业务按钮码）：
#   exam:generate    组卷生成（与 exam.py require_perm 对齐：admin/teacher/region_admin）
#   grading:submit   提交批改（与 grading.py require_perm 对齐：admin/teacher）
#   grading:review   评分批改（与 grading.py PUT 对齐：admin/teacher）
# school/region_admin 对批改、school 对组卷均无按钮——页面可见但动作隐藏，
# 由后端 require_perm 兜底拦截，前端 v-has-permission 只做体验层。
ROLE_MENU_MAP = {
    "role_admin": [m[0] for m in MENUS],  # 全菜单 + 全按钮
    "role_region_admin": [
        m[0] for m in MENUS
        if m[3] in ("DIR", "MENU")
        and not (m[0] == "m_sys" or (m[1] == "m_sys"))
    ] + ["m_ops_exam_gen"],  # 区域管理员：仅区域运营，不含系统设置；保留组卷动作
    "role_school": [
        "m_ops", "m_ops_tenant", "m_ops_knowledge", "m_ops_analytics",
        "m_ops_wrong", "m_ops_exam", "m_ops_grading", "m_ops_reports", "m_ops_compliance",
        "m_ops_errordist", "m_ops_practice",
    ],  # 学校：页面全览，无任何按钮动作
    "role_teacher": [
        "m_ops", "m_ops_knowledge", "m_ops_analytics", "m_ops_wrong",
        "m_ops_exam", "m_ops_grading", "m_ops_reports",
        "m_ops_errordist", "m_ops_practice",
        "m_ops_exam_gen", "m_ops_grading_submit", "m_ops_grading_review",
    ],
    "role_parent": ["m_ops", "m_ops_reports"],
}


def build_seed_regions() -> List[dict]:
    return [
        {"region_id": f"rgn_{code}", "region_code": code, "region_name": name, "sort": i}
        for i, (code, name) in enumerate(REGIONS, 1)
    ]


def build_seed_roles() -> List[dict]:
    return [
        {"role_id": rid, "role_code": code, "role_name": name}
        for rid, code, name in ROLES
    ]


def build_seed_menus() -> List[dict]:
    return [
        {
            "menu_id": mid, "parent_id": pid, "menu_name": name, "menu_type": mtype,
            "path": path, "component": comp, "icon": icon, "sort": sort,
            "permission_code": perm,
        }
        for mid, pid, name, mtype, path, comp, icon, sort, perm in MENUS
    ]


def build_seed_role_menus() -> List[dict]:
    rows = []
    for role_id, menu_ids in ROLE_MENU_MAP.items():
        for mid in menu_ids:
            rows.append({"role_id": role_id, "menu_id": mid})
    return rows


def build_seed_admin_user(password_hash: str) -> dict:
    return {
        "user_id": "u_admin",
        "username": "admin@tes.edu",
        "password_hash": password_hash,
        "tenant_id": None,
        "region_id": None,
    }


def build_seed_region_admin_user(password_hash: str) -> dict:
    return {
        "user_id": "u_region_admin",
        "username": "region_admin@tes.edu",
        "password_hash": password_hash,
        "tenant_id": None,
        "region_id": "rgn_east",  # 示例：华东区域管理员
    }


def build_seed_school_user(password_hash: str) -> dict:
    return {
        "user_id": "u_school",
        "username": "school@tes.edu",
        "password_hash": password_hash,
        "tenant_id": "demo",  # 与 auth.py _FAKE_USERS 的 _DEMO_TENANT 一致
        "region_id": "rgn_east",
    }


def build_seed_teacher_user(password_hash: str) -> dict:
    return {
        "user_id": "u_teacher",
        "username": "teacher@tes.edu",
        "password_hash": password_hash,
        "tenant_id": "demo",
        "region_id": "rgn_east",
    }


def build_seed_parent_user(password_hash: str) -> dict:
    return {
        "user_id": "u_parent",
        "username": "parent@tes.edu",
        "password_hash": password_hash,
        "tenant_id": "demo",
        "region_id": "rgn_east",
    }


def build_seed_user_roles() -> List[dict]:
    """五角色演示账号的用户-角色关联（平台级 + 租户级各一）。

    SysUserRole.tenant_id 是主键且非空；平台角色无具体租户，用 "platform" 占位，
    避免种子漏建关联导致 /login 查不到 role_code（role=None → 鉴权链路全断）。
    租户级账号（school/teacher/parent）挂 "demo" 租户——不建 sys_user 行时，
    这些账号走 auth.py _FAKE_USERS 明文兜底，无权角色关联，/menu/my 返回空
    （T-AM7 联调实测：teacher/parent 动态菜单 0 节点），故补种子。
    """
    return [
        {"user_id": "u_admin", "role_id": "role_admin", "tenant_id": "platform"},
        {"user_id": "u_region_admin", "role_id": "role_region_admin", "tenant_id": "platform"},
        {"user_id": "u_school", "role_id": "role_school", "tenant_id": "demo"},
        {"user_id": "u_teacher", "role_id": "role_teacher", "tenant_id": "demo"},
        {"user_id": "u_parent", "role_id": "role_parent", "tenant_id": "demo"},
    ]


async def init_rbac(engine) -> None:
    """幂等建表。"""
    from app.models import rbac_models  # noqa: F401 注册表
    from app.models.rbac_models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_rbac(db, password_hash: str, bcrypt=None) -> dict:
    """写入种子（幂等：已存在则跳过）。返回统计。"""
    from sqlalchemy import select, text

    from app.models.rbac_models import (
        SysMenu, SysRegion, SysRole, SysRoleMenu, SysUser, SysUserRole,
    )

    stats = {"regions": 0, "roles": 0, "menus": 0, "role_menus": 0, "users": 0, "user_roles": 0}

    # 区域
    existing = (await db.execute(select(SysRegion.region_id))).scalars().all()
    if not existing:
        for r in build_seed_regions():
            db.add(SysRegion(**r))
        stats["regions"] = len(REGIONS)

    # 角色
    existing = (await db.execute(select(SysRole.role_id))).scalars().all()
    if not existing:
        for r in build_seed_roles():
            db.add(SysRole(**r))
        stats["roles"] = len(ROLES)

    # 菜单（T-AM7 起按 menu_id 逐条 upsert：线上库已有旧种子时也能补插新节点，
    # 如 m_ops_errordist / m_ops_practice；已存在的行不改动，保持运维手工调整）
    existing = set((await db.execute(select(SysMenu.menu_id))).scalars().all())
    new_menus = [m for m in build_seed_menus() if m["menu_id"] not in existing]
    for m in new_menus:
        db.add(SysMenu(**m))
    stats["menus"] = len(new_menus)

    # 角色-菜单授权（同样 upsert：补插缺失的 (role_id, menu_id) 对，
    # 如给 school/teacher 补新菜单授权；不删除既有授权）
    existing_rm = {
        tuple(r) for r in (
            await db.execute(select(SysRoleMenu.role_id, SysRoleMenu.menu_id))
        ).all()
    }
    new_rm = [
        rm for rm in build_seed_role_menus()
        if (rm["role_id"], rm["menu_id"]) not in existing_rm
    ]
    for rm in new_rm:
        db.add(SysRoleMenu(**rm))
    stats["role_menus"] = len(new_rm)

    # 用户（五角色演示账号；按 username 逐条 upsert，线上旧库补插不漏）
    all_seed_users = [
        build_seed_admin_user(password_hash),
        build_seed_region_admin_user(password_hash),
        build_seed_school_user(password_hash),
        build_seed_teacher_user(password_hash),
        build_seed_parent_user(password_hash),
    ]
    existing = set((await db.execute(select(SysUser.username))).scalars().all())
    new_users = [u for u in all_seed_users if u["username"] not in existing]
    for u in new_users:
        db.add(SysUser(**u))
    stats["users"] = len(new_users)

    # 用户-角色关联（按 (user_id, role_id) upsert：补插缺失对，不删既有授权）
    existing_ur = {
        tuple(r) for r in (
            await db.execute(select(SysUserRole.user_id, SysUserRole.role_id))
        ).all()
    }
    new_ur = [
        ur for ur in build_seed_user_roles()
        if (ur["user_id"], ur["role_id"]) not in existing_ur
    ]
    for ur in new_ur:
        db.add(SysUserRole(**ur))
    stats["user_roles"] = len(new_ur)

    await db.commit()
    return stats
