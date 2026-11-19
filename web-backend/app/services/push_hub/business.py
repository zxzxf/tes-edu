"""业务钩子：判分完成 / 作业下发 → 推送中心 emit。

设计红线（业务旁路，绝不反压主链路）：
- fire-and-forget：asyncio.create_task + **独立会话**，不碰调用方的事务；
- 主题不存在/未启用/平台应用未建/总开关关闭 → 静默跳过（老环境、第三方 demo
  无 push 表时业务照常跑）；
- 任何异常只记日志，绝不抛回判分/下发接口；
- **幂等锚点 msg_id**：同一 submission / assignment 只推一次，重复判分（学生
  重交补跑、人工改判）不会重复打扰学生。

receiver 口径：sub.student_id = user["user_id"] = JWT uid = 网关注入的 X-User-Id，
与学生端收件箱 _receiver 完全同源（见 push_inbox.py）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("push_hub.business")

_pending: set[asyncio.Task] = set()


async def _emit_to_platform_app(
    *,
    topic_code: str,
    receiver: str,
    title: str,
    body: str,
    msg_id: str,
    payload: Optional[dict] = None,
) -> None:
    """查平台自有应用（owner_account_id IS NULL）+ 启用主题，命中才 emit。"""
    if not settings.push_business_notify_enabled or not receiver:
        return
    try:
        from sqlalchemy import select

        from app.core.db import SessionLocal
        from app.models.push_hub import PushApp, PushTopic
        from app.services.push_hub.emit import emit

        async with SessionLocal() as db:
            app_id = (
                await db.execute(
                    select(PushApp.app_id)
                    .where(PushApp.owner_account_id.is_(None))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if app_id is None:
                return
            enabled = (
                await db.execute(
                    select(PushTopic.enabled).where(
                        PushTopic.app_id == app_id,
                        PushTopic.topic_code == topic_code,
                    )
                )
            ).scalar_one_or_none()
            if not enabled:
                return
            await emit(
                db,
                app_id=app_id,
                topic_code=topic_code,
                receiver=receiver,
                title=title,
                body=body,
                payload=payload,
                msg_id=msg_id,
            )
    except Exception:  # noqa: BLE001 推送是旁路，任何失败都不影响业务
        logger.exception(
            "业务推送钩子失败（业务不受影响）topic=%s receiver=%s msg=%s",
            topic_code, receiver, msg_id,
        )


def _schedule(**kwargs) -> None:
    task = asyncio.create_task(_emit_to_platform_app(**kwargs))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def grade_ready(
    *,
    submission_id: int,
    student_id: str,
    score: int,
    total: int,
    status: str,
) -> None:
    """判分定稿 → grade 主题（微信引流 + App 收件箱双通道由主题路由决定）。

    只推终态 graded；manual_pending（含人工题）不推，等教师复核完再推。
    msg_id 用 submission_id 锚定：重交补跑/人工改判不重复推送同一份成绩。
    """
    if status != "graded":
        return
    _schedule(
        topic_code="grade",
        receiver=student_id,
        title="成绩已出",
        body=f"作业已批阅：{score}/{total} 分，点击查看解析。",
        payload={"kind": "grade", "submission_id": submission_id,
                 "score": score, "total": total},
        msg_id=f"grade-sub-{submission_id}",
    )


def homework_assigned(
    *,
    assignment_id: int,
    student_id: str,
) -> None:
    """作业下发到具体学生 → homework 主题。

    仅 target_type=student 精确推送；target_type=grade 是年级段批量下发，
    学生端作业列表本身会显示新卷，不依赖站内推送（避免无差别打扰）。
    """
    _schedule(
        topic_code="homework",
        receiver=student_id,
        title="新作业",
        body="老师布置了一份新作业，记得按时完成。",
        payload={"kind": "homework", "assignment_id": assignment_id},
        msg_id=f"homework-asg-{assignment_id}-{student_id}",
    )
