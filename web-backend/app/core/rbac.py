"""RBAC 角色常量与权限类型。

角色定义需与 Go 网关签发 JWT 时的 role 声明保持一致；网关在转发时把
用户身份以 X-User-Id / X-User-Role 头透传进来。

T-AM1 扩展（2026-10-25）：
- 新增 REGION_ADMIN（区域管理员）；废弃 RESEARCHER（从未落地菜单授权，
  其能力已由 admin + teacher + region_admin 覆盖）；STUDENT 仅 App 端，
  不作为 Web 菜单角色。
- ROLE_MODULES 保留作过渡兼容（require_role 粗粒度仍可用），但正式授权
  改为 DB 驱动的 permission_code 集合（见 app/core/permission.py）。
"""

from enum import Enum
from typing import Dict, List


class Role(str, Enum):
    ADMIN = "admin"              # 超级管理员（系统设置全权）
    REGION_ADMIN = "region_admin"  # 区域管理员（按 region_id 管辖本区域）
    SCHOOL = "school"            # 学校（仅管本校）
    TEACHER = "teacher"          # 教师（教学侧，本班）
    PARENT = "parent"            # 家长（只读子女）
    STUDENT = "student"          # 学生（C 端，仅 App 使用，不进 Web 菜单）


# 保留作过渡兼容（require_role 粗粒度仍可用）；新权限引擎以 permission_code 为准。
ROLE_MODULES: Dict[Role, List[str] | str] = {
    Role.ADMIN: "*",
    Role.REGION_ADMIN: ["tenant", "knowledge", "analytics", "wrong_questions",
                        "exam", "grading", "reports", "compliance"],
    Role.SCHOOL: ["knowledge", "analytics", "wrong_questions", "exam",
                  "grading", "reports", "compliance"],
    Role.TEACHER: ["analytics", "wrong_questions", "exam", "grading", "reports"],
    Role.PARENT: ["reports"],
    Role.STUDENT: [],
}

# DB 驱动权限集合（T-AM2 起生效）：role_code -> 聚合的 permission_code 集合。
# 占位为过渡值，运行期由 get_user_permissions() 从 DB 聚合；此处保留静态兜底
# 以便无 DB 时单测仍可断言结构（具体授权以 sys_role_menu 为准）。
ROLE_PERMISSIONS: Dict[Role, List[str]] = {
    Role.ADMIN: ["*:*"],  # 管理员全权
    Role.REGION_ADMIN: [],
    Role.SCHOOL: [],
    Role.TEACHER: [],
    Role.PARENT: ["reports:view"],
    Role.STUDENT: [],
}
