"""RBAC 角色常量。

角色定义需与 Go 网关签发 JWT 时的 role 声明保持一致；网关在转发时把
用户身份以 X-User-Id / X-User-Role 头透传进来。
"""
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"            # 超级管理员
    RESEARCHER = "researcher"  # 教研员（知识库/图谱维护、合规初审）
    TEACHER = "teacher"        # 教师（批改/布置/学情）
    PARENT = "parent"          # 家长（周报查看）
    STUDENT = "student"        # 学生（C 端，仅 App 使用）


# 角色 -> 可访问模块（用于依赖校验；占位示例，后续按真实权限矩阵细化）
ROLE_MODULES: dict[Role, list[str] | str] = {
    Role.ADMIN: "*",
    Role.RESEARCHER: ["knowledge", "compliance"],
    Role.TEACHER: ["analytics", "wrong_questions", "exam", "grading", "reports"],
    Role.PARENT: ["reports"],
    Role.STUDENT: [],
}
