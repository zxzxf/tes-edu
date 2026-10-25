"""模块6：家长端周报。

按学生 + 周期生成学习周报（完成量、薄弱点、学习投入、建议），数据来源为
student_mastery（掌握度）与 student_events（学情事件流水），均为真实数据。
另提供 /reports/weekly/page 直接返回自包含 HTML 报告页，供家长在网关后直接打开。

T-AM8 脱敏：题干/步骤标签/建议等自由文本可能夹带 PII（如应用题里的手机号），
对外返回前统一过 app.core.masking（与网关 pii.go 同规则）。学生级归属校验
（家长↔学生绑定）属 P4，落地前周报仅做角色准入 + 自由文本脱敏。
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import masking
from app.core.db import get_db
from app.core.rbac import Role
from app.core.security import require_role
from app.services import reports as reports_svc

router = APIRouter(prefix="/reports", tags=["reports"])

# 周报中可能夹带 PII 的自由文本字段（结构字段如 student_id/计数/日期不脱敏）
_FREETEXT_KEYS = (
    "question", "step_label", "headline", "advice", "label", "suggestion",
    "knowledge_point", "subject", "main_error_type", "error_type",
)


@router.get("/weekly")
async def weekly(
    student_id: str = Query(...),
    period_days: int = Query(7, ge=1, le=60),
    tenant_id: str = Query(None),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """学情周报（JSON）。数据真实计算自 student_mastery + student_events。"""
    report = await reports_svc.compute_weekly_report(db, student_id, period_days, tenant_id)
    return masking.mask_free_text_fields(report, _FREETEXT_KEYS)


@router.get("/weekly/page", response_class=HTMLResponse)
async def weekly_page(
    student_id: str = Query(...),
    period_days: int = Query(7, ge=1, le=60),
    tenant_id: str = Query(None),
    user=Depends(require_role(Role.ADMIN, Role.TEACHER, Role.PARENT)),
    db: AsyncSession = Depends(get_db),
):
    """学情周报（HTML 页）。自包含、无外部依赖，家长在网关后直接打开即可查看。"""
    report = await reports_svc.compute_weekly_report(db, student_id, period_days, tenant_id)
    report = masking.mask_free_text_fields(report, _FREETEXT_KEYS)
    return HTMLResponse(content=reports_svc._render_html(report))
