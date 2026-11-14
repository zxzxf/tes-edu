"""合规审核队列：把「产生待审内容」这件事收敛成一个幂等入口。

存在的原因（2026-11-14 线上排查）：合规审核页一直显示「暂无待审核内容」，
生产库 ``compliance_items`` 0 行——全仓库只有手工 ``POST /compliance`` 能写，
没有任何业务链路会入队。本模块给三条真实生产者提供统一落库口：

1. 教师录题 ``services/question_entry.py`` → ``source=question``
2. 采集入库 ``crawl_service/intake.py``     → ``source=crawl``
3. 网关敏感词拦截 ``POST /compliance/events`` → ``source=gateway``

红线：

* **tenant_id 只取服务端可信值**（登录态 / 采集任务 / 网关注入头），
  绝不从请求体读，否则一个教师就能把内容塞进别校的审核队列。
* **幂等键** ``(tenant_id, dedup_key)``：同一道题、同一天同一路径同一敏感词
  只入队一次。重复录题/重跑采集/网关重试都不该刷出一屏重复待办。
* **本模块不 commit**：题目与待审项必须在同一个事务里落库，
  由调用方统一提交。入队失败由调用方用 SAVEPOINT 降级，不能把题目一起 rollback。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import ComplianceItem

SOURCE_LABEL = {
    "question": "教师录题",
    "crawl": "采集入库",
    "gateway": "网关内容安全",
    "manual": "手工创建",
}


async def enqueue(
    db: AsyncSession,
    *,
    tenant_id: Optional[str],
    dedup_key: Optional[str],
    content: str,
    item_type: str = "题目",
    status: str = "pending",
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
    source: Optional[str] = None,
) -> Optional[ComplianceItem]:
    """幂等落一条待审项；已存在返回 ``None``。**不 commit**。

    ``tenant_id`` 或 ``dedup_key`` 为空时直接跳过：没有租户的行谁也看不到，
    没有幂等键的行重跑就会重复，两者都不如不入队。
    """
    if not tenant_id or not dedup_key or not content:
        return None

    exists = (
        await db.execute(
            select(ComplianceItem.id).where(
                ComplianceItem.tenant_id == tenant_id,
                ComplianceItem.dedup_key == dedup_key,
            )
        )
    ).scalars().first()
    if exists is not None:
        return None

    obj = ComplianceItem(
        tenant_id=tenant_id,
        type=item_type,
        content=content,
        status=status,
        ref_type=ref_type,
        ref_id=ref_id,
        dedup_key=dedup_key,
        source=source,
    )
    db.add(obj)
    await db.flush()
    return obj


async def enqueue_question(
    db: AsyncSession, question, *, source: str
) -> Optional[ComplianceItem]:
    """题库 draft 行 → 待审项。``question`` 只需有 id / tenant_id / stem。"""
    qid = getattr(question, "id", None)
    tenant_id = getattr(question, "tenant_id", None)
    stem = getattr(question, "stem", None) or ""
    if qid is None or not tenant_id:
        return None
    return await enqueue(
        db,
        tenant_id=tenant_id,
        dedup_key=f"question:{qid}",
        content=stem,
        item_type="题目",
        ref_type="question",
        ref_id=qid,
        source=source,
    )
