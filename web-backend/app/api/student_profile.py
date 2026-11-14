"""M0-3 学生年级归属：教师维护 + 批量导入接口。

鉴权用网关透传的身份头（与现有 admin 路由一致）；租户口径统一取
``X-Tenant-Id``，缺失时落试点校（与 exam 模块 ``_tenant_of`` 同口径）。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import student_profile as svc
from app.services.scope import resolve_read_scope
from app.services.student_profile import DEFAULT_GRADE, GRADES

router = APIRouter(prefix="/students", tags=["student-profile"])
#: 学生自查（挂 /api/student 后为 /api/student/profile/me）
student_router = APIRouter(prefix="/profile", tags=["student-profile"])

UPDATE_ROLES = (Role.TEACHER, Role.ADMIN, Role.SCHOOL, Role.REGION_ADMIN)


class ProfileUpsert(BaseModel):
    student_id: str
    grade: str


class ProfileBulk(BaseModel):
    profiles: List[ProfileUpsert]


def _tenant_of(user: dict) -> str:
    return user.get("tenant_id") or "school_pilot_01"


@student_router.get("/me")
async def my_profile(
    user=Depends(require_role(Role.STUDENT)),
    db: AsyncSession = Depends(get_db),
):
    """学生查自己的年级（App 展示 + 排障用）。未配置时如实标记 fallback。"""
    grade = await svc.get_grade(db, user["user_id"], _tenant_of(user))
    return {
        "student_id": user["user_id"],
        "tenant_id": _tenant_of(user),
        "grade": grade,
        "fallback": grade == DEFAULT_GRADE,
        "grades": list(GRADES),
    }


@router.get("/profiles")
async def list_profiles(
    grade: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(require_role(*UPDATE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    # admin 全量 / region_admin 本区域全部租户 / 其余本租户。
    scope = await resolve_read_scope(user, db)
    tenant_id = scope["tenant"] if scope["mode"] == "tenant" else None
    tenant_ids = scope.get("tenants") if scope["mode"] == "tenants" else None
    rows = await svc.list_profiles(
        db, tenant_id=tenant_id, tenant_ids=tenant_ids, grade=grade, limit=limit
    )
    return {
        "items": [svc.profile_out(r) for r in rows],
        "counts": await svc.count_by_grade(db, tenant_id=tenant_id, tenant_ids=tenant_ids),
        "grades": list(GRADES),
        "default_grade": DEFAULT_GRADE,
    }


@router.put("/profiles/{student_id}")
async def upsert_profile(
    student_id: str,
    body: ProfileUpsert,
    user=Depends(require_role(*UPDATE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    if body.student_id and body.student_id != student_id:
        raise HTTPException(status_code=400, detail="路径与请求体的 student_id 不一致")
    try:
        row = await svc.upsert(db, student_id, _tenant_of(user), body.grade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    await db.refresh(row)
    return svc.profile_out(row)


@router.post("/profiles/bulk")
async def bulk_profiles(
    body: ProfileBulk,
    user=Depends(require_role(*UPDATE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    raw = [p.model_dump() for p in body.profiles]
    rows, errors = svc.normalize_rows(raw)
    stats = await svc.bulk_upsert(db, _tenant_of(user), rows)
    await db.commit()
    return {"received": len(raw), "errors": errors, **stats}


@router.post("/profiles/upload")
async def upload_profiles(
    file: UploadFile = File(...),
    user=Depends(require_role(*UPDATE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """上传名册（.csv/.json）。单行脏值不阻断整批，逐行返回错误。"""
    content = await file.read()
    try:
        raw = svc.parse_import(content, file.filename or "")
    except Exception as e:  # noqa: BLE001 解析失败要给教师可读的原因，而不是 500
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")
    rows, errors = svc.normalize_rows(raw)
    stats = await svc.bulk_upsert(db, _tenant_of(user), rows)
    await db.commit()
    return {
        "filename": file.filename,
        "received": len(raw),
        "errors": errors,
        **stats,
    }
