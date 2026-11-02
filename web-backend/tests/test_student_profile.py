"""M0-3 学生年级归属：年级归一 / 租户默认兜底 / 按年级下发可见性。

背景：成绩下发按年级段（高一/高二/高三）进行，但学生身份一直是裸字符串
`student_id`，没有年级属性。此前 `_visible_to` 把「高三」写死，导致高一/
高二学生永远看不到自己的卷（而高三学生会收到本租户所有年级的卷）。

这里锁住三条口径：
1. 年级输入归一（``高三`` / ``高3`` / ``3`` / ``senior3`` 都收敛到同一值）；
2. 未配置年级的学生按**该租户的默认年级**兜底，不能因此看不到卷；
3. 年级不匹配的卷不可见——这是年级段下发唯一真正的过滤点。
"""
import pytest

from app.api.exam import _visible_to
from app.models.ops import ExamAssignment
from app.services.student_profile import (
    DEFAULT_GRADE,
    grade_matches,
    normalize_grade,
)

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


# ------------------------------------------------------------------ 归一
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("高三", "高三"),
        ("高3", "高三"),
        ("高中三年级", "高三"),
        ("3", "高三"),
        (" senior3 ", "高三"),
        ("S3", "高三"),
        ("grade12", "高三"),
        ("高二", "高二"),
        ("高2", "高二"),
        ("高一", "高一"),
        ("高1", "高一"),
    ],
)
def test_normalize_grade_aliases(raw, expected):
    assert normalize_grade(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "大学", "七年级", "abc"])
def test_normalize_grade_rejects_unknown(raw):
    """不认识的年级返回 None，由调用方决定兜底，绝不猜成高三。"""
    assert normalize_grade(raw) is None


def test_grade_matches_accepts_alias_of_student_grade():
    assert grade_matches("高三", "高3") is True
    assert grade_matches("高一", "高二") is False


def test_default_grade_is_senior3_for_mvp():
    """首期 MVP 只有高三题库，默认年级仍取高三（可用配置覆盖）。"""
    assert DEFAULT_GRADE == "高三"


# ------------------------------------------------- 按年级下发：可见性
def test_senior2_student_can_see_senior2_assignment():
    a = _assign(target_grade="高二")
    assert _visible_to(a, _user(), grade="高二") is True


def test_senior2_student_cannot_see_senior3_assignment():
    a = _assign(target_grade="高三")
    assert _visible_to(a, _user(), grade="高二") is False


def test_senior3_student_cannot_see_senior2_assignment():
    """回归：此前年级写死高三，高一/高二的卷对高三学生也会露出。"""
    a = _assign(target_grade="高二")
    assert _visible_to(a, _user(), grade="高三") is False


def test_grade_alias_in_assignment_still_matches():
    a = _assign(target_grade="高3")
    assert _visible_to(a, _user(), grade="高三") is True


def test_student_target_bypasses_grade_filter():
    """指定学生下发只看学生 id，不该被年级过滤掉。"""
    a = _assign(target_type="student", target_grade=None, target_student="student@tes.edu")
    assert _visible_to(a, _user(), grade="高二") is True
