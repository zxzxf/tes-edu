"""题库接口：教研侧录入（M0-1）+ 查询（消费侧）。"""
from typing import List, Optional

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

router = APIRouter(prefix="/questions", tags=["questions"])

#: 录题角色：教师录自己的题，管理员/区域管理员代录。学生与家长无此权限。
_ENTRY_ROLES = (Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)


def _tenant_of(user: dict) -> str:
    """读取侧租户口径：与 exam / student_profile 保持一致。

    网关漏注入 ``X-Tenant-Id`` 时按试点校兜底；**不可**退化成不过滤，
    否则一次配置遗漏就会把别校题库整表暴露出去。
    """
    return user.get("tenant_id") or "school_pilot_01"


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
    limit: int = Query(50, le=200),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """最近录入优先；**只返回本租户题目**（M0-4 隔离验收）。"""
    return await svc.list_questions(db, limit=limit, tenant_id=_tenant_of(user))


@router.get("/search", response_model=List[QuestionOut])
async def search(
    q: Optional[str] = Query(None, description="关键词（题干/解析模糊匹配）"),
    subject: Optional[str] = None,
    type: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    limit: int = Query(50, le=200),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.REGION_ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """关键词/学科/题型/知识点检索；**只在本租户题库内查**（M0-4）。"""
    return await svc.search_questions(
        db,
        q=q,
        subject=subject,
        qtype=type,
        knowledge_point=knowledge_point,
        limit=limit,
        tenant_id=_tenant_of(user),
    )
