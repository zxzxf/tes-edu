"""T-D3：LLM 用量与预算告警 API。

挂载点：main.py 以 prefix="/api/admin" 注册本路由器 → 实际路径 /api/admin/llm-usage。
鉴权：ADMIN / TEACHER（内网信任网关注入的 X-User-Role 头）。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import llm_usage as usage_svc

router = APIRouter(prefix="/llm-usage", tags=["llm-usage"])


@router.get("")
async def llm_usage(
    model: Optional[str] = Query(None, description="按模型过滤；不传为全部"),
    from_dt: Optional[datetime] = Query(None, description="ISO8601，过滤 created_at 下界"),
    to_dt: Optional[datetime] = Query(None, description="ISO8601，过滤 created_at 上界"),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """LLM 用量总览 + 按模型/按日聚合 + 日预算告警状态。

    返回字段：
    - ``total``   ：calls / real_calls / cache_hit_rate / degraded_rate /
                    prompt+completion tokens / cost / avg_latency_ms / errors
    - ``by_model``、``by_day``：同样口径的分组
    - ``budget``  ：当日成本对 ``LLM_DAILY_BUDGET`` 的判定（ok/warn/over + 动作）
    """
    return await usage_svc.compute_llm_usage_for(db, from_dt, to_dt, model)


@router.get("/budget")
async def llm_budget(
    user=Depends(require_role(Role.ADMIN, Role.TEACHER)),
    db: AsyncSession = Depends(get_db),
):
    """只看预算状态（当日成本 / 预算 / 占比 / 是否触发 force_simple）。"""
    data = await usage_svc.compute_llm_usage_for(db)
    return data["budget"]
