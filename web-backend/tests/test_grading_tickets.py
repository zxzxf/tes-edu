"""人工评定工单（M2-5/M2-6）口径回归。

DB 真值（unique 约束、汇总回写、跨租户）在 137 容器用真库端到端验；
这里固化**纯逻辑口径**，防止后来改坏：

1. 判定 → (correct, partial) 映射与「partial 不计分」口径；
2. 汇总**全量重算**（不是增量加减）；
3. 跨租户工单一律 404；
4. ``accept_answer`` 追加答案值幂等去重、多空题按空拆。
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import grading as api
from app.api.grading import (
    _accept_answer,
    _recalc_submission,
    _ticket_or_404,
    _ticket_out,
    _verdict_flags,
)
from app.core.db import get_db

PILOT = "school_pilot_01"


# -------------------------------------------------------------------------- fakes
class _Res:
    """伪 SQLAlchemy 结果对象（按需提供 one / rows）。"""

    def __init__(self, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _FakeDB:
    """按调用顺序吐结果；记录 add/commit，够验证写路径即可。"""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _item(correct, status):
    return SimpleNamespace(correct=correct, status=status)


# -------------------------------------------------------------------------- 判定映射
def test_verdict_mapping():
    assert _verdict_flags("correct") == (True, False)
    assert _verdict_flags("wrong") == (False, False)
    # partial：标记部分分但不计分（与选择题漏选同口径）
    assert _verdict_flags("partial") == (False, True)


# -------------------------------------------------------------------------- 汇总重算
def test_recalc_all_reviewed_marks_graded():
    sub = SimpleNamespace(correct_count=0, score=0, total_count=0, status="manual_pending")
    items = [
        _item(True, "auto"),
        _item(False, "auto"),
        _item(True, "manual_reviewed"),
    ]
    db = _FakeDB([_Res(one=sub), _Res(rows=items)])
    out = asyncio.run(_recalc_submission(db, 7))
    assert out.correct_count == 2          # 全量重算，含人工判定那道
    assert out.total_count == 3
    assert out.score == 2
    assert out.status == "graded"          # 不再有待人工的题


def test_recalc_keeps_manual_pending_until_all_reviewed():
    sub = SimpleNamespace(correct_count=0, score=0, total_count=0, status="graded")
    items = [_item(True, "manual_reviewed"), _item(None, "manual_pending")]
    db = _FakeDB([_Res(one=sub), _Res(rows=items)])
    out = asyncio.run(_recalc_submission(db, 8))
    assert out.correct_count == 1
    assert out.status == "manual_pending"  # 还有一道没判，整卷仍是待人工


def test_recalc_all_wrong_is_zero_not_stale():
    """改判成错：计数必须归零，不能残留上一次的分数（增量加减的典型事故）。"""
    sub = SimpleNamespace(correct_count=9, score=9, total_count=9, status="graded")
    items = [_item(False, "manual_reviewed"), _item(False, "auto")]
    db = _FakeDB([_Res(one=sub), _Res(rows=items)])
    out = asyncio.run(_recalc_submission(db, 9))
    assert out.correct_count == 0 and out.score == 0
    assert out.total_count == 2


# -------------------------------------------------------------------------- 租户隔离
def test_ticket_cross_tenant_404():
    db = _FakeDB([_Res(one=SimpleNamespace(tenant_id="demo"))])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_ticket_or_404(db, 1, {"tenant_id": PILOT}))
    assert ei.value.status_code == 404


def test_ticket_missing_404():
    db = _FakeDB([_Res(one=None)])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_ticket_or_404(db, 1, {"tenant_id": PILOT}))
    assert ei.value.status_code == 404


def test_ticket_same_tenant_ok():
    t = SimpleNamespace(id=3, tenant_id=PILOT)
    db = _FakeDB([_Res(one=t)])
    assert asyncio.run(_ticket_or_404(db, 3, {"tenant_id": PILOT})) is t


# -------------------------------------------------------------------------- 答案值追加
def _ticket(answer="-2", ref="2", answer_values=None):
    q = SimpleNamespace(id=11, answer=ref, answer_values=answer_values)
    t = SimpleNamespace(
        id=5, submission_item_id=17, paper_question_id=11, student_answer=answer
    )
    return q, t


def test_accept_answer_appends_and_records_revision():
    q, t = _ticket(answer="-2", ref="2")
    db = _FakeDB([_Res(one=q)])
    added = asyncio.run(_accept_answer(db, t, {"user_id": "t1", "role": "teacher"}))
    assert added == ["-2"]
    assert q.answer_values == ["2", "-2"]      # 标答预展开值保留
    assert any(getattr(o, "action", None) == "add_value" for o in db.added)


def test_accept_answer_idempotent():
    q, t = _ticket(answer="-2", ref="2", answer_values=["2", "-2"])
    db = _FakeDB([_Res(one=q)])
    assert asyncio.run(_accept_answer(db, t, {"user_id": "t1"})) == []
    assert db.added == []                       # 没有新增就不留痕


def test_accept_answer_multi_blank_splits_per_blank():
    q, t = _ticket(answer="2；5", ref="2;3")
    db = _FakeDB([_Res(one=q)])
    added = asyncio.run(_accept_answer(db, t, {"user_id": "t1"}))
    assert added == ["5"]                       # 2 已存在，只补第二空
    assert q.answer_values == ["2", "3", "5"]


# -------------------------------------------------------------------------- 响应映射
def test_ticket_out_maps_fields():
    from datetime import datetime, timezone

    t = SimpleNamespace(
        id=1, status="reviewed", qtype="blank", seq=3, student_id="s1",
        assignment_id=7, paper_id=9, student_answer="-2", reason="含字母",
        verdict="correct", comment="写法也对", assignee="t1", reviewed_by="t1",
        reviewed_at=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
        created_at=None,
    )
    out = _ticket_out(t, "高三·数学·纯填空卷")
    assert out.ticket_id == 1 and out.status == "reviewed"
    assert out.paper_title == "高三·数学·纯填空卷"
    assert out.reviewed_at.startswith("2026-10-29T12:00")
    assert out.created_at is None


# -------------------------------------------------------------------------- 鉴权
def _client():
    app = FastAPI()
    app.include_router(api.router, prefix="/api/admin")
    app.dependency_overrides[get_db] = lambda: None
    return TestClient(app)


def test_list_tickets_requires_identity():
    resp = _client().get("/api/admin/grading/tickets")
    assert resp.status_code in (401, 403)


def test_list_tickets_rejects_student_role():
    resp = _client().get(
        "/api/admin/grading/tickets",
        headers={"X-User-Id": "s1", "X-User-Role": "student"},
    )
    assert resp.status_code == 403


def test_judge_rejects_invalid_verdict():
    resp = _client().post(
        "/api/admin/grading/tickets/1/judge",
        json={"verdict": "maybe"},
        headers={"X-User-Id": "t1", "X-User-Role": "teacher", "X-Tenant-Id": PILOT},
    )
    assert resp.status_code == 422          # Literal 锁三值


def test_ticket_detail_declares_fields_api_returns():
    """防回归：schema 漏声明字段时 pydantic 会**静默丢弃**，前端拿不到还以为接口没实现。

    真实踩过：``answer_values_effective`` 只在 API 里构造、schema 忘了声明，
    结果教师端「多空题答案值不生效」的提示永远不出现。
    """
    from app.models.schemas import GradingTicketDetail

    for f in (
        "stem", "options", "correct_answer", "analysis", "knowledge_points",
        "answer_values", "answer_values_effective", "revisions", "freeze",
    ):
        assert f in GradingTicketDetail.model_fields, f"GradingTicketDetail 缺字段 {f}"
