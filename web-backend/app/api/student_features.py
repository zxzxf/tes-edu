"""学生端能力扩展 API（W3：T-F / T-S / T-A）。

学生端（网关 /api/student/ 路由 mode=jwt_c 透传学生身份）：
- GET  /features           当前学生对各功能开关的生效集（按 tenant/region/class 解析）
- GET  /anti-fatigue       当前生效的防疲劳配置
- /projects*              学生自建学习项目 CRUD + 任务清单（按 student_id+tenant_id 隔离）

管理端（网关 /api/admin/ 路由，require_perm system:feature:manage）：
- GET  /features          全部开关定义 + 各范围 override（供后台表格）
- PUT  /features          写某范围 override（按角色范围边界校验）
- GET  /anti-fatigue      查某范围防疲劳配置
- PUT  /anti-fatigue      写某范围防疲劳配置
"""

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.db import get_db
from app.core.permission import require_perm
from app.core.rbac import Role
from app.core.security import get_current_user
from app.models import student_features as m
from app.services import student_features as svc

# ------------------------- 学生端 router -------------------------
student_router = APIRouter(tags=["student-features"])


class ProjectIn(BaseModel):
    title: str = Field(..., max_length=255)
    description: Optional[str] = None
    goal: Optional[str] = None
    kp_code: Optional[str] = None


class ProjectTaskIn(BaseModel):
    title: str = Field(..., max_length=255)
    due_at: Optional[str] = None  # ISO 字符串
    sort: int = 0


class ProjectTaskPatch(BaseModel):
    title: Optional[str] = None
    done: Optional[bool] = None
    due_at: Optional[str] = None
    sort: Optional[int] = None


def _to_iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _project_out(p, tasks) -> dict:
    total = len(tasks)
    done = sum(1 for t in tasks if t.done)
    return {
        "project_id": p.project_id,
        "title": p.title,
        "description": p.description,
        "goal": p.goal,
        "kp_code": p.kp_code,
        "status": p.status,
        "progress": (done / total) if total else 0.0,
        "task_count": total,
        "task_done": done,
        "created_at": _to_iso(p.created_at),
        "updated_at": _to_iso(p.updated_at),
        "tasks": [
            {
                "task_id": t.task_id,
                "title": t.title,
                "done": t.done,
                "due_at": _to_iso(t.due_at),
                "sort": t.sort,
            }
            for t in sorted(tasks, key=lambda x: x.sort or 0)
        ],
    }


@student_router.get("/features")
async def student_features(
    platform: str = Query("web", pattern="^(web|app)$"),
    x_user_class: Optional[str] = Header(None, alias="X-User-Class"),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """学生端拉取功能开关生效集（#2 总闸消费）。"""
    features = await svc.get_features_for_student(
        db,
        tenant_id=user.get("tenant_id"),
        region_id=user.get("region_id"),
        class_id=x_user_class,
        platform=platform,
    )
    return {"features": features}


@student_router.get("/anti-fatigue")
async def student_anti_fatigue(
    x_user_class: Optional[str] = Header(None, alias="X-User-Class"),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """学生端拉取当前生效的防疲劳配置（#3）。"""
    cfg = await _resolve_anti_fatigue(
        db,
        tenant_id=user.get("tenant_id"),
        region_id=user.get("region_id"),
        class_id=x_user_class,
    )
    return {
        "enabled": cfg.enabled,
        "interval_min": cfg.interval_min,
        "break_min": cfg.break_min,
        "mode": cfg.mode,
        "message": cfg.message,
    }


@student_router.get("/projects")
async def list_projects(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    rows = (
        await db.execute(
            select(m.StudentProject).where(
                m.StudentProject.student_id == user["user_id"],
                m.StudentProject.tenant_id == user.get("tenant_id"),
            )
        )
    ).scalars().all()
    out = []
    for p in rows:
        tasks = (
            await db.execute(
                select(m.StudentProjectTask).where(
                    m.StudentProjectTask.project_id == p.project_id
                )
            )
        ).scalars().all()
        out.append(_project_out(p, list(tasks)))
    return out


@student_router.post("/projects", status_code=201)
async def create_project(
    body: ProjectIn,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    pid = uuid.uuid4().hex
    p = m.StudentProject(
        project_id=pid,
        student_id=user["user_id"],
        tenant_id=user.get("tenant_id") or "",
        title=body.title,
        description=body.description,
        goal=body.goal,
        kp_code=body.kp_code,
    )
    db.add(p)
    await db.commit()
    return {"project_id": pid}


@student_router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    p = await _owned_project(db, project_id, user)
    tasks = await _project_tasks(db, project_id)
    return _project_out(p, tasks)


@student_router.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectIn,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    p = await _owned_project(db, project_id, user)
    p.title = body.title
    p.description = body.description
    p.goal = body.goal
    p.kp_code = body.kp_code
    await db.commit()
    return {"ok": True}


@student_router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    p = await _owned_project(db, project_id, user)
    await db.execute(
        delete(m.StudentProjectTask).where(
            m.StudentProjectTask.project_id == project_id
        )
    )
    await db.delete(p)
    await db.commit()
    return {"ok": True}


@student_router.get("/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _owned_project(db, project_id, user)  # 鉴权归属
    tasks = await _project_tasks(db, project_id)
    return [
        {"task_id": t.task_id, "title": t.title, "done": t.done,
         "due_at": _to_iso(t.due_at), "sort": t.sort}
        for t in sorted(tasks, key=lambda x: x.sort or 0)
    ]


@student_router.post("/projects/{project_id}/tasks", status_code=201)
async def add_task(
    project_id: str,
    body: ProjectTaskIn,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _owned_project(db, project_id, user)
    tid = uuid.uuid4().hex
    due = None
    if body.due_at:
        from datetime import datetime
        due = datetime.fromisoformat(body.due_at)
    t = m.StudentProjectTask(
        task_id=tid, project_id=project_id,
        title=body.title, due_at=due, sort=body.sort,
    )
    db.add(t)
    await db.commit()
    return {"task_id": tid}


@student_router.put("/projects/{project_id}/tasks/{task_id}")
async def patch_task(
    project_id: str,
    task_id: str,
    body: ProjectTaskPatch,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _owned_project(db, project_id, user)
    t = (
        await db.execute(
            select(m.StudentProjectTask).where(
                m.StudentProjectTask.task_id == task_id,
                m.StudentProjectTask.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    if body.title is not None:
        t.title = body.title
    if body.done is not None:
        t.done = body.done
    if body.sort is not None:
        t.sort = body.sort
    if body.due_at is not None:
        from datetime import datetime
        t.due_at = datetime.fromisoformat(body.due_at) if body.due_at else None
    await db.commit()
    return {"ok": True}


@student_router.delete("/projects/{project_id}/tasks/{task_id}")
async def delete_task(
    project_id: str,
    task_id: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    await _owned_project(db, project_id, user)
    await db.execute(
        delete(m.StudentProjectTask).where(
            m.StudentProjectTask.task_id == task_id,
            m.StudentProjectTask.project_id == project_id,
        )
    )
    await db.commit()
    return {"ok": True}


# ------------------------- 管理端 router -------------------------
admin_router = APIRouter(tags=["admin-features"])


class FeatureOverrideIn(BaseModel):
    feature_key: str
    scope_type: str = Field(..., pattern="^(global|region|tenant|class)$")
    scope_id: str = ""
    enabled: bool


class AntiFatigueIn(BaseModel):
    scope_type: str = Field(..., pattern="^(global|region|tenant|class)$")
    scope_id: str = ""
    enabled: bool = True
    interval_min: int = 25
    break_min: int = 5
    mode: str = Field("remind", pattern="^(remind|enforce)$")
    message: str = "该休息一下啦，远眺放松眼睛，保护视力~"


@admin_router.get("/features")
async def admin_list_features(
    user: dict = Depends(require_perm("system:feature:manage")),
    db=Depends(get_db),
):
    switches = (await db.execute(select(m.FeatureSwitch))).scalars().all()
    overrides = (await db.execute(select(m.FeatureSwitchOverride))).scalars().all()
    ov_by_key: Dict[str, list] = {}
    for ov in overrides:
        ov_by_key.setdefault(ov.feature_key, []).append(
            {"scope_type": ov.scope_type, "scope_id": ov.scope_id, "enabled": ov.enabled}
        )
    return [
        {
            "feature_key": s.feature_key,
            "feature_name": s.feature_name,
            "platform": s.platform,
            "default_on": s.default_on,
            "description": s.description,
            "overrides": ov_by_key.get(s.feature_key, []),
        }
        for s in switches
    ]


@admin_router.put("/features")
async def admin_set_feature(
    body: FeatureOverrideIn,
    user: dict = Depends(require_perm("system:feature:manage")),
    db=Depends(get_db),
):
    _check_scope_write(user, body.scope_type, body.scope_id)
    # upsert override
    existing = (
        await db.execute(
            select(m.FeatureSwitchOverride).where(
                m.FeatureSwitchOverride.feature_key == body.feature_key,
                m.FeatureSwitchOverride.scope_type == body.scope_type,
                m.FeatureSwitchOverride.scope_id == (body.scope_id or ""),
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.enabled = body.enabled
        existing.updated_by = user.get("user_id")
    else:
        db.add(
            m.FeatureSwitchOverride(
                feature_key=body.feature_key,
                scope_type=body.scope_type,
                scope_id=body.scope_id or "",
                enabled=body.enabled,
                updated_by=user.get("user_id"),
            )
        )
    await db.commit()
    return {"ok": True}


@admin_router.get("/anti-fatigue")
async def admin_get_anti_fatigue(
    scope_type: str = Query("global", pattern="^(global|region|tenant|class)$"),
    scope_id: str = "",
    user: dict = Depends(require_perm("system:feature:manage")),
    db=Depends(get_db),
):
    cfg = await _anti_fatigue_row(db, scope_type, scope_id)
    if not cfg:
        cfg = m.AntiFatigueConfig(scope_type=scope_type, scope_id=scope_id or "")
    return {
        "scope_type": cfg.scope_type,
        "scope_id": cfg.scope_id,
        "enabled": cfg.enabled,
        "interval_min": cfg.interval_min,
        "break_min": cfg.break_min,
        "mode": cfg.mode,
        "message": cfg.message,
    }


@admin_router.put("/anti-fatigue")
async def admin_set_anti_fatigue(
    body: AntiFatigueIn,
    user: dict = Depends(require_perm("system:feature:manage")),
    db=Depends(get_db),
):
    _check_scope_write(user, body.scope_type, body.scope_id)
    existing = await _anti_fatigue_row(db, body.scope_type, body.scope_id)
    if existing:
        existing.enabled = body.enabled
        existing.interval_min = body.interval_min
        existing.break_min = body.break_min
        existing.mode = body.mode
        existing.message = body.message
    else:
        db.add(
            m.AntiFatigueConfig(
                scope_type=body.scope_type,
                scope_id=body.scope_id or "",
                enabled=body.enabled,
                interval_min=body.interval_min,
                break_min=body.break_min,
                mode=body.mode,
                message=body.message,
            )
        )
    await db.commit()
    return {"ok": True}


# ------------------------- 内部辅助 -------------------------
async def _owned_project(db, project_id: str, user: dict):
    p = (
        await db.execute(
            select(m.StudentProject).where(
                m.StudentProject.project_id == project_id,
                m.StudentProject.student_id == user["user_id"],
                m.StudentProject.tenant_id == user.get("tenant_id"),
            )
        )
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="project not found")
    return p


async def _project_tasks(db, project_id: str):
    return list(
        (
            await db.execute(
                select(m.StudentProjectTask).where(
                    m.StudentProjectTask.project_id == project_id
                )
            )
        ).scalars().all()
    )


async def _anti_fatigue_row(db, scope_type: str, scope_id: str):
    return (
        await db.execute(
            select(m.AntiFatigueConfig).where(
                m.AntiFatigueConfig.scope_type == scope_type,
                m.AntiFatigueConfig.scope_id == (scope_id or ""),
            )
        )
    ).scalar_one_or_none()


async def _resolve_anti_fatigue(db, *, tenant_id=None, region_id=None, class_id=None):
    scopes = []
    if class_id:
        scopes.append(("class", class_id))
    if tenant_id:
        scopes.append(("tenant", tenant_id))
    if region_id:
        scopes.append(("region", region_id))
    scopes.append(("global", ""))
    rows = (await db.execute(select(m.AntiFatigueConfig))).scalars().all()
    by_key = {(r.scope_type, r.scope_id or ""): r for r in rows}
    for st, sid in scopes:
        if (st, sid) in by_key:
            return by_key[(st, sid)]
    return m.AntiFatigueConfig(
        scope_type="global", scope_id="", enabled=True,
        interval_min=25, break_min=5, mode="remind",
        message="该休息一下啦，远眺放松眼睛，保护视力~",
    )


def _check_scope_write(user: dict, scope_type: str, scope_id: str):
    """校验写入范围不超过当前角色管辖边界（#2 角色×开关矩阵）。"""
    role = user.get("role")
    if role == Role.ADMIN.value:
        return
    if role == Role.REGION_ADMIN.value:
        if scope_type == "global":
            return
        if scope_type in ("region", "tenant", "class"):
            rid = user.get("region_id")
            if scope_type == "region" and scope_id != rid:
                raise HTTPException(status_code=403, detail="超出本区域管辖")
            return
        raise HTTPException(status_code=403, detail="区域管理员不可写该范围")
    if role == Role.SCHOOL.value:
        if scope_type == "tenant" and scope_id == user.get("tenant_id"):
            return
        raise HTTPException(status_code=403, detail="学校仅可设本租户")
    if role == Role.TEACHER.value:
        if scope_type == "class":
            return  # 教师可设本班（班级绑定表属 P4，暂宽松）
        raise HTTPException(status_code=403, detail="教师仅可设本班")
    raise HTTPException(status_code=403, detail="无功能开关管理权限")
