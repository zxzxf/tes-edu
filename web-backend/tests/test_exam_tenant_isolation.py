"""学生端作业下发的租户隔离（M1-5 补漏，2026-10-29）。

背景：`list_student_exams` 原先只按 `target_grade` 命中，不看租户。
年级段下发没有学生 id，于是 **demo 租户教师发的卷会出现在试点校学生的列表里**，
且拿着别人的 `assignment_id` 能直接取卷、翻成绩。

`_visible_to` 是纯函数（只读对象字段），可以脱离 DB 单测。
"""
from app.api.exam import _visible_to
from app.models.ops import ExamAssignment

PILOT = "school_pilot_01"


def _assign(**kw) -> ExamAssignment:
    base = dict(
        id=1,
        paper_id=11,
        tenant_id=PILOT,
        target_type="grade",
        target_grade="高三",
        target_student=None,
    )
    base.update(kw)
    return ExamAssignment(**base)


def _user(tenant=PILOT, uid="student@tes.edu"):
    return {"user_id": uid, "tenant_id": tenant, "role": "student"}


def test_same_tenant_grade_assignment_visible():
    assert _visible_to(_assign(), _user()) is True


def test_other_tenant_grade_assignment_hidden():
    """核心回归：别的学校按年级段发的卷，不能出现在本校本年级列表里。"""
    assert _visible_to(_assign(tenant_id="demo"), _user()) is False


def test_missing_tenant_on_assignment_is_hidden():
    """老数据/脏数据没有 tenant_id 时按不可见处理（宁可少发，不能串校）。"""
    assert _visible_to(_assign(tenant_id=None), _user()) is False


def test_student_target_matching_self_visible():
    a = _assign(target_type="student", target_grade=None, target_student="student@tes.edu")
    assert _visible_to(a, _user()) is True


def test_student_target_other_student_hidden():
    a = _assign(target_type="student", target_grade=None, target_student="student_002")
    assert _visible_to(a, _user()) is False


def test_student_target_cross_tenant_hidden():
    a = _assign(
        tenant_id="demo", target_type="student", target_grade=None,
        target_student="student@tes.edu",
    )
    assert _visible_to(a, _user()) is False


def test_grade_assignment_other_grade_hidden():
    assert _visible_to(_assign(target_grade="高二"), _user()) is False


def test_user_without_tenant_falls_back_to_pilot():
    """网关注入头缺失时按试点校兜底，与 tenant_id 读取口径一致。"""
    assert _visible_to(_assign(), {"user_id": "student@tes.edu"}) is True
    assert _visible_to(_assign(tenant_id="demo"), {"user_id": "student@tes.edu"}) is False
