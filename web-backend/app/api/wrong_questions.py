"""模块3：错题本管理。

教师/家长查看学生错题；录入错题（可由 Agent 后端自动同步）。
2026-10-23：原内存 _STORE 占位改为真实持久化（表 wrong_questions）。
2026-10-24（S5 ④）：列表返回时按 (content_hash, wrong_step_no) 关联 course_kb_steps，
补上该步的正确做法(correction)、步骤详解与知识点卡，让错题本"知道错在哪一步、怎么改"。
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import business_tenant_id, require_role
from app.models.schemas import WrongQuestion, WrongQuestionOut
from app.services import ops
from app.services.scope import resolve_read_scope

router = APIRouter(prefix="/wrong-questions", tags=["wrong-questions"])


@router.get("", response_model=List[WrongQuestionOut])
async def list_wrong(
    student_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    # 2026-11-14：region_admin 的授权矩阵里一直有「错题本」菜单（m_ops_wrong），
    # 但接口白名单不含它 → 菜单可见、点进去 403 的死链。错题本与
    # /analytics/summary、/analytics/error-distribution、/compliance 同属
    # 平台级只读运营视图，按同一 A 方案放行 region_admin 只读（写入仍只给
    # admin/teacher）；这比撤菜单更一致，也不必 DELETE 线上 sys_role_menu 行。
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    # 迁移 0023：列表按登录态租户隔离。admin 全量；region_admin 按本区域
    # 全部租户；其余角色锁本校。与 compliance、papers 的 resolve_read_scope 口径一致。
    scope = await resolve_read_scope(user, db)
    tenant_id = scope["tenant"] if scope["mode"] == "tenant" else None
    tenant_ids = scope.get("tenants") if scope["mode"] == "tenants" else None
    rows = await ops.list_wrong(
        db, student_id, limit=limit, tenant_id=tenant_id, tenant_ids=tenant_ids
    )
    return await ops.enrich_wrong(db, rows)


@router.post("", response_model=WrongQuestion)
async def add_wrong(
    w: WrongQuestion,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    payload = w.model_dump(exclude={"id"})
    # 租户以登录态为唯一权威：请求体里带 tenant_id 也不能写到别校；
    # business_tenant_id 顺带把 platform 占位归一到真实兜底租户。
    payload["tenant_id"] = business_tenant_id(user)
    return await ops.create_wrong(db, payload)
