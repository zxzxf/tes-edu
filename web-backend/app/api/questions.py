"""题库接口：教研侧录入（M0-1）+ 查询（消费侧）。"""
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.models.course_kb import (
    QuestionCreate,
    QuestionCreateResult,
    QuestionOut,
)
from app.services import course_kb as svc
from app.services import question_entry
from app.services.scope import resolve_read_scope

router = APIRouter(prefix="/questions", tags=["questions"])

#: 录题角色：教师录自己的题，管理员/区域管理员代录。学生与家长无此权限。
_ENTRY_ROLES = (Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)


async def _resolve_scope_args(user: dict, db: AsyncSession) -> tuple:
    """题库读侧租户口径（list/search 两个读接口共用）。

    admin 全量；region_admin 返回本区域全部租户（tenant_ids）；其余返回本租户。
    返回 (tenant_id, tenant_ids)：恰其一为非空，service 层据此下推过滤。
    """
    scope = await resolve_read_scope(user, db)
    if scope["mode"] == "tenant":
        return scope["tenant"], None
    if scope["mode"] == "tenants":
        return None, scope["tenants"]
    return None, None


@router.post("", response_model=QuestionCreateResult, summary="教师录单题（M0-1）")
async def create_question(
    payload: QuestionCreate,
    user=Depends(require_role(*_ENTRY_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """录 1 道题：写 ``course_kb``（``status=draft``）+ 双写向量。

    - ``content_hash`` 去重：与 Excel/JSON 导入同一指纹算法，重复录不会产生第二行；
    - 向量化失败不影响入库，题目仍为 ``draft``，响应里 ``embedding.ok=false``
      并给出原因（配额/密钥/端点），补齐向量后走审核接口发布。
    """
    return await question_entry.create_questions(db, [payload], user)


@router.post("/batch", response_model=QuestionCreateResult, summary="教师批量录题（M0-1）")
async def create_questions_batch(
    payload: List[QuestionCreate],
    user=Depends(require_role(*_ENTRY_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """批量录题；逐题幂等，已存在的题按 ``duplicated`` 计数返回，不覆盖原题。

    与 ``POST /api/admin/ingest/questions/json`` 的区别：那条链路面向文件/大批量
    导入并可走 Kafka 异步；本接口面向教师在页面上的即时录入，同步返回每题结果
    （含向量状态），便于前端逐行提示。
    """
    if not payload:
        return {
            "received": 0,
            "created": 0,
            "duplicated": 0,
            "items": [],
            "embedding": {"ok": True, "model": "", "source": "none", "reason": "", "written": 0},
        }
    return await question_entry.create_questions(db, payload, user)


@router.get("", response_model=List[QuestionOut])
async def list_questions(
    limit: Annotated[int, Query(le=200)] = 50,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """最近录入优先；admin 全量 / region_admin 本区域 / 其余本租户（M0-4 隔离）。"""
    tenant_id, tenant_ids = await _resolve_scope_args(user, db)
    return await svc.list_questions(db, limit=limit, tenant_id=tenant_id, tenant_ids=tenant_ids)


@router.get("/search", response_model=List[QuestionOut])
async def search(
    q: Annotated[Optional[str], Query(description="关键词（题干/解析模糊匹配）")] = None,
    subject: Optional[str] = None,
    type: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    grade: Optional[str] = None,
    difficulty_min: Annotated[Optional[int], Query(ge=1, le=5)] = None,
    difficulty_max: Annotated[Optional[int], Query(ge=1, le=5)] = None,
    status: Annotated[Optional[str], Query(pattern="^(draft|published|quarantine)$")] = None,
    answerable: Annotated[bool, Query(description="M4-1 选题：只返回可入卷的题")] = False,
    limit: Annotated[int, Query(le=200)] = 50,
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """关键词/学科/题型/知识点/年级/难度检索；admin 全量 / region_admin 本区域 / 其余本租户（M0-4）。"""
    tenant_id, tenant_ids = await _resolve_scope_args(user, db)
    return await svc.search_questions(
        db,
        q=q,
        subject=subject,
        qtype=type,
        knowledge_point=knowledge_point,
        grade=grade,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        status=status,
        answerable=answerable,
        limit=limit,
        tenant_id=tenant_id,
        tenant_ids=tenant_ids,
    )
