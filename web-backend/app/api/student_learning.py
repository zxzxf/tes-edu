"""学生端学习资产接口：错题本云同步 + 背诵卡间隔复习。

挂在根路径（router 自带 ``/learning`` 前缀），与既有 ``learning/*`` 同源：
Go 网关对该前缀按 JWT 鉴权并注入 X-User-Id，学生只能操作自己的数据。

**越权边界**：角色为 STUDENT 时，student_id 一律取网关注入的 user_id，
忽略请求参数（否则学生 A 传 B 的学号就能看别人的错题）；教师/管理员
才允许显式指定 student_id 查别人。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import drill as drill_svc
from app.services import wrongbook as wrong_svc

router = APIRouter(prefix="/learning", tags=["student-learning"])


# --------------------------------------------------------------------------- 契约

class WrongPushItem(BaseModel):
    client_id: str = ""
    question: str
    my_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    knowledge_point: Optional[str] = None
    error_type: Optional[str] = None
    comment: Optional[str] = None
    correction: Optional[str] = None
    first_wrong_step: Optional[int] = None
    updated_at: int = 0            # epoch 毫秒，冲突判定用
    deleted: bool = False          # true = 客户端已删除，服务端软删


class WrongSyncRequest(BaseModel):
    student_id: str = ""
    items: List[WrongPushItem] = Field(default_factory=list)


class WrongSyncResponse(BaseModel):
    items: List[dict] = Field(default_factory=list)
    server_time: int = 0


class DrillPushItem(BaseModel):
    client_id: str = ""
    kp: Optional[str] = None
    front: str
    back: str
    source: Optional[str] = None


class DrillPushRequest(BaseModel):
    student_id: str = ""
    items: List[DrillPushItem] = Field(default_factory=list)


class DrillReviewRequest(BaseModel):
    rating: int = 0                # 0 忘了 / 3 模糊 / 4 会了 / 5 太简单
    rated_at: Optional[int] = None  # 客户端复习时间（离线补传时才需要）


class OkResponse(BaseModel):
    ok: bool = False


# --------------------------------------------------------------------------- 工具

def _student_id(user: dict, requested: str = "") -> str:
    """学生角色强制用自己的 user_id，教师/管理员可查别人。"""
    if user.get("role") == Role.STUDENT.value:
        return user["user_id"]
    return (requested or "").strip() or user["user_id"]


def _now_ms() -> int:
    from datetime import datetime, timezone

    return int(datetime.now(timezone.utc).timestamp() * 1000)


# --------------------------------------------------------------------------- 错题本

@router.get("/wrong-questions", response_model=WrongSyncResponse)
async def list_wrong_questions(
    student_id: str = Query(""),
    since: int = Query(0),
    kp: Optional[str] = Query(None),
    error_type: Optional[str] = Query(None),
    limit: int = Query(200),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """增量拉取错题（含 since 之后的 tombstone，客户端据此同步删除）。"""
    sid = _student_id(user, student_id)
    items = await wrong_svc.list_sync(db, sid, since_ms=since, kp=kp, error_type=error_type, limit=limit)
    return WrongSyncResponse(items=items, server_time=_now_ms())


@router.post("/wrong-questions/sync", response_model=WrongSyncResponse)
async def sync_wrong_questions(
    req: WrongSyncRequest,
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """批量 upsert（按 client_id 幂等），回吐服务端当前全量——客户端直接整体合并。"""
    sid = _student_id(user, req.student_id)
    items = await wrong_svc.upsert_sync(
        db, sid, [it.model_dump() for it in req.items], tenant_id=user.get("tenant_id")
    )
    return WrongSyncResponse(items=items, server_time=_now_ms())


@router.delete("/wrong-questions/{wrong_id}", response_model=OkResponse)
async def delete_wrong_question(
    wrong_id: int,
    student_id: str = Query(""),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """软删（保留 tombstone，否则同步会把删掉的题又推回来）。"""
    sid = _student_id(user, student_id)
    ok = await wrong_svc.soft_delete(db, sid, wrong_id)
    if not ok:
        raise HTTPException(status_code=404, detail="wrong question not found")
    return OkResponse(ok=True)


@router.get("/wrong-questions/stats")
async def wrong_stats(
    student_id: str = Query(""),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """错题统计：总数 / 错因分布 / 知识点分布 / 掌握度。"""
    sid = _student_id(user, student_id)
    return await wrong_svc.stats(db, sid)


# --------------------------------------------------------------------------- 背诵卡

@router.get("/drill-cards")
async def list_drill_cards(
    student_id: str = Query(""),
    due_only: int = Query(1),
    limit: int = Query(20),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """到期卡队列（due_only=1 时按到期时间升序取前 limit 张）。"""
    sid = _student_id(user, student_id)
    items, due, total = await drill_svc.list_cards(
        db, sid, due_only=bool(due_only), limit=limit
    )
    return {"items": items, "due_count": due, "total": total}


@router.post("/drill-cards")
async def push_drill_cards(
    req: DrillPushRequest,
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """批量建卡（按 client_id 幂等）。新卡当天到期，立刻能背。"""
    sid = _student_id(user, req.student_id)
    items = await drill_svc.push_cards(
        db, sid, [it.model_dump() for it in req.items], tenant_id=user.get("tenant_id")
    )
    _, due, total = await drill_svc.list_cards(db, sid, due_only=False, limit=len(items) or 1)
    return {"items": items, "due_count": due, "total": total}


@router.post("/drill-cards/{card_id}/review")
async def review_drill_card(
    card_id: int,
    req: DrillReviewRequest,
    student_id: str = Query(""),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """提交复习评分：按服务端权威值重算排程（客户端离线算错不污染库）。"""
    from datetime import datetime, timezone

    sid = _student_id(user, student_id)
    rated_at = (
        datetime.fromtimestamp(req.rated_at / 1000, tz=timezone.utc)
        if req.rated_at
        else None
    )
    out = await drill_svc.review_card(db, sid, card_id, req.rating, rated_at=rated_at)
    if out is None:
        raise HTTPException(status_code=404, detail="drill card not found")
    return out


@router.get("/drill-cards/stats")
async def drill_stats(
    student_id: str = Query(""),
    user=Depends(require_role(Role.STUDENT, Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """背诵卡概览：总数 / 到期 / 新卡 / 近 30 次复习记住率。"""
    sid = _student_id(user, student_id)
    return await drill_svc.stats(db, sid)
