"""M0-3 学生年级归属：一张轻量表 + 年级归一。

设计口径（方案书 A11 / §6-12 定案）：

* **不建班级**，也不并入 ``sys_user``（后者是后台管理账号体系，语义不同）。
  学生身份继续沿用裸字符串 ``student_id``，另建 ``student_profile`` 承载年级。
* 年级值域与 ``course_kb.grade`` 对齐：高一 / 高二 / 高三。
* **未配置年级的学生按租户默认年级兜底**——否则学生登录后一份卷都看不到，
  这类"配置漏了"不该表现成"没有作业"。
* 年级归一放在服务层：教师批量导入的表格里 ``高3`` / ``高中三年级`` / ``3``
  都出现过，存进去必须收敛成同一个值，否则按年级筛选必然漏。
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops import StudentProfile

#: 年级值域（与 course_kb.grade 对齐）
GRADES = ("高一", "高二", "高三")

#: 默认年级：首期 MVP 只有高三题库；可用 STUDENT_DEFAULT_GRADE 覆盖。
DEFAULT_GRADE = os.getenv("STUDENT_DEFAULT_GRADE", "高三").strip() or "高三"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 年级归一

# 阿拉伯数字/英文 -> 中文年级。``senior3`` / ``grade12`` 之类的历史写法也收。
_NUM_TO_GRADE = {
    "1": "高一", "2": "高二", "3": "高三",
    "10": "高一", "11": "高二", "12": "高三",
    "一": "高一", "二": "高二", "三": "高三",
}


def normalize_grade(raw: object) -> Optional[str]:
    """任意年级写法 -> 高一/高二/高三；无法识别返回 None。

    **不猜**：``大学`` / ``七年级`` / 空串一律 None，由调用方决定兜底。
    认不出来的值如果硬塞成高三，会让这些学生收到不该做的卷——比看不到更糟。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(" ", "").replace("　", "").lower()

    if s in GRADES:
        return s

    # 高3 / 高 三 / 高中三年级 / 高中3年级 / 高三上
    m = re.fullmatch(r"高(?:中)?([一二三123])(?:年级|级|年|上|下|学期)?", s)
    if m:
        return _NUM_TO_GRADE.get(m.group(1))
    # 三年级 / 3年级 / 三年级上
    m = re.fullmatch(r"([一二三123])年级(?:级|上|下|学期)?", s)
    if m:
        return _NUM_TO_GRADE.get(m.group(1))
    # senior3 / senior-3 / grade3 / s3 / g3 / 3
    m = re.fullmatch(r"(?:senior|grade|s|g)[-_]?([123]|1[012])", s)
    if m:
        return _NUM_TO_GRADE.get(m.group(1))
    if s in _NUM_TO_GRADE:
        return _NUM_TO_GRADE[s]
    return None


def grade_matches(assignment_grade: object, student_grade: object) -> bool:
    """下发年级与学生年级是否命中（两边都先归一，别名互通）。"""
    a = normalize_grade(assignment_grade)
    b = normalize_grade(student_grade)
    if a is None or b is None:
        return False
    return a == b


def display_grade(raw: object) -> str:
    """展示/兜底用：认得出就用归一值，认不出原样返回（空则给默认年级）。"""
    return normalize_grade(raw) or (str(raw or "").strip() or DEFAULT_GRADE)


# ---------------------------------------------------------------- 数据访问

async def get_grade(
    db: AsyncSession, student_id: str, tenant_id: str, *, default: Optional[str] = None
) -> str:
    """取学生年级；没有档案时返回租户默认年级（**不写库**）。

    只读路径不该顺手 INSERT：学生可能只是来看一眼，写库会把错误兜底值固化。
    """
    row = (
        await db.execute(
            select(StudentProfile).where(
                StudentProfile.student_id == student_id,
                StudentProfile.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        g = normalize_grade(row.grade)
        if g:
            return g
    return default or DEFAULT_GRADE


def profile_out(row: StudentProfile) -> dict:
    return {
        "student_id": row.student_id,
        "tenant_id": row.tenant_id,
        "grade": row.grade,
        "grade_normalized": normalize_grade(row.grade),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert(
    db: AsyncSession, student_id: str, tenant_id: str, grade: object
) -> StudentProfile:
    """单条 upsert：年级归一后写入；无法识别直接抛 ValueError，不让脏值入库。"""
    sid = (student_id or "").strip()
    if not sid:
        raise ValueError("student_id 不能为空")
    g = normalize_grade(grade)
    if g is None:
        raise ValueError(f"无法识别的年级：{grade!r}（可选高一/高二/高三）")

    row = (
        await db.execute(
            select(StudentProfile).where(
                StudentProfile.student_id == sid,
                StudentProfile.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = StudentProfile(student_id=sid, tenant_id=tenant_id, grade=g)
        db.add(row)
    else:
        row.grade = g
        row.updated_at = _utcnow()
    return row


async def list_profiles(
    db: AsyncSession,
    *,
    tenant_id: Optional[str] = None,
    tenant_ids: Optional[list] = None,
    grade: Optional[str] = None,
    limit: int = 200,
) -> List[StudentProfile]:
    stmt = select(StudentProfile)
    if tenant_ids:
        stmt = stmt.where(StudentProfile.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(StudentProfile.tenant_id == tenant_id)
    g = normalize_grade(grade)
    if g:
        stmt = stmt.where(StudentProfile.grade == g)
    rows = (
        await db.execute(
            stmt.order_by(StudentProfile.student_id).limit(max(1, min(limit, 1000)))
        )
    ).scalars().all()
    return list(rows)


async def count_by_grade(
    db: AsyncSession, tenant_id: Optional[str] = None, tenant_ids: Optional[list] = None
) -> dict:
    """各年级人数（未配置年级的学生单独计，便于教师发现漏配）。"""
    stmt = select(StudentProfile.grade, StudentProfile.student_id)
    if tenant_ids:
        stmt = stmt.where(StudentProfile.tenant_id.in_(tenant_ids))
    elif tenant_id is not None:
        stmt = stmt.where(StudentProfile.tenant_id == tenant_id)
    rows = (await db.execute(stmt)).all()
    out = {g: 0 for g in GRADES}
    out["未配置"] = 0
    for grade, _sid in rows:
        g = normalize_grade(grade)
        if g:
            out[g] += 1
        else:
            out["未配置"] += 1
    return out


# ---------------------------------------------------------------- 批量导入

def parse_import(content: bytes, filename: str) -> List[dict]:
    """CSV / JSON -> [{student_id, grade}] 原始行（未校验）。

    列名兼容 ``student_id`` / ``学号`` / ``学生`` 与 ``grade`` / ``年级``。
    中文 Windows 导出的 CSV 常带 BOM，统一 ``utf-8-sig`` 解码。
    """
    name = (filename or "").lower()
    text = content.decode("utf-8-sig", errors="replace")
    if name.endswith(".json"):
        import json

        data = json.loads(text or "[]")
        if isinstance(data, dict):
            data = data.get("students") or data.get("profiles") or []
        return [dict(r) for r in data if isinstance(r, dict)]

    reader = csv.DictReader(io.StringIO(text))
    return [dict(r) for r in reader]


_ID_KEYS = ("student_id", "学号", "学生", "student", "id")
_GRADE_KEYS = ("grade", "年级", "年级段")


def _pick_ci(row: dict, keys: Sequence[str]):
    lut = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for k in keys:
        if k.lower() in lut:
            return lut[k.lower()]
    return None


def normalize_rows(raw_rows: Iterable[dict]) -> tuple[List[dict], List[dict]]:
    """批量行归一：返回 (可写入行, 错误行)。

    **整批不因单行脏值失败**：教师导一个班的名册，某个年级写成"四年级"时，
    应该告诉他这一行错了、其余照常入库，而不是整批回滚重来。
    """
    ok: List[dict] = []
    errors: List[dict] = []
    seen: set = set()
    for idx, r in enumerate(raw_rows, start=1):
        sid = _pick_ci(r, _ID_KEYS)
        grade = _pick_ci(r, _GRADE_KEYS)
        sid_s = str(sid).strip() if sid is not None else ""
        if not sid_s:
            errors.append({"row": idx, "reason": "缺少学号（student_id/学号）"})
            continue
        g = normalize_grade(grade)
        if g is None:
            errors.append(
                {"row": idx, "student_id": sid_s, "reason": f"无法识别的年级：{grade!r}"}
            )
            continue
        if sid_s in seen:
            errors.append({"row": idx, "student_id": sid_s, "reason": "同一批次重复学号"})
            continue
        seen.add(sid_s)
        ok.append({"student_id": sid_s, "grade": g})
    return ok, errors


async def bulk_upsert(
    db: AsyncSession, tenant_id: str, rows: Sequence[dict]
) -> dict:
    """批量 upsert（已在调用方事务内）。先一次查出既有行，避免 N 次往返。"""
    sids = [r["student_id"] for r in rows]
    existing = {}
    if sids:
        for row in (
            await db.execute(
                select(StudentProfile).where(
                    StudentProfile.tenant_id == tenant_id,
                    StudentProfile.student_id.in_(sids),
                )
            )
        ).scalars().all():
            existing[row.student_id] = row

    created = 0
    updated = 0
    now = _utcnow()
    for r in rows:
        row = existing.get(r["student_id"])
        if row is None:
            db.add(
                StudentProfile(
                    student_id=r["student_id"], tenant_id=tenant_id, grade=r["grade"]
                )
            )
            created += 1
        else:
            if normalize_grade(row.grade) != r["grade"]:
                updated += 1
            row.grade = r["grade"]
            row.updated_at = now
    return {"created": created, "updated": updated, "total": len(rows)}
