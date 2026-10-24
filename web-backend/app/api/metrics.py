"""T-C4：P2 引导式对话六项埋点指标 API。

挂载点：main.py 以 prefix="/api/admin" 注册本路由器 → 实际路径 /api/admin/p2-metrics。
鉴权：ADMIN / TEACHER（内网信任网关注入的 X-User-Role 头）。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import p2_metrics as metrics_svc

router = APIRouter(prefix="/p2-metrics", tags=["p2-metrics"])


@router.get("")
async def p2_metrics(
    student_id: Optional[str] = Query(None, description="不传则按租户/全量聚合"),
    tenant_id: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None, description="ISO8601，过滤事件 occurred_at 下界"),
    to_dt: Optional[datetime] = Query(None, description="ISO8601，过滤事件 occurred_at 上界"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """六项 P2 引导质量指标 + M2 体验红线判定。

    返回：首轮直给率 / 提示有效推进率 / 单题平均交互轮次 / 规避率 / 放弃率 /
    规避+放弃率 / 挫败信号率 / 延迟迁移正确率，附 _counts 原始计数与 red_lines 红线。
    """
    metrics = await metrics_svc.compute_p2_metrics_for(
        db, student_id, tenant_id, from_dt, to_dt, subject
    )
    metrics["red_lines"] = metrics_svc.evaluate_red_lines(metrics)
    return metrics
