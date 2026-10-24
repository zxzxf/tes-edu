"""掌握度更新算法（T-B2）纯函数测试（不连库，对齐 web-backend 测试哲学）。

覆盖：答对祖先上溢 / 答错只扣该步且不污染已掌握祖先 / 前置薄弱点回溯 /
      部分步错 / 遗忘衰减 / 事件→步骤结果推导。
"""
from datetime import datetime, timedelta, timezone

from app.services import mastery as m

# DAG：A(先学) <- B <- C(后学)  边 (B,A) 表示 A 是 B 的前置；(C,B) 表示 B 是 C 的前置
EDGES = [("B", "A"), ("C", "B")]


# ---------------- DAG 遍历 ---------------- #
def test_walk_up():
    assert m.walk_up(EDGES, "C") == {"B", "A"}
    assert m.walk_up(EDGES, "B") == {"A"}
    assert m.walk_up(EDGES, "A") == set()
    assert m.walk_up(EDGES, "Z") == set()  # 孤立点


# ---------------- 答对：祖先上溢 ---------------- #
def test_correct_overflows_to_ancestors():
    steps = [m.StepResult(step_no=1, is_correct=True, kp="C")]
    plan = m.plan_mastery_updates(steps, EDGES)
    assert plan.updates["C"]["correct"] == 1
    assert plan.updates["B"]["correct"] == 1  # 祖先 B 上溢
    assert plan.updates["A"]["correct"] == 1  # 祖先 A 上溢
    # 答错才会出现 wrong，这里不应有任何 wrong
    for v in plan.updates.values():
        assert v["wrong"] == 0
    assert plan.weak_prereqs == set()


# ---------------- 答错：只扣该步，不污染已掌握祖先 ---------------- #
def test_wrong_only_penalizes_kp_not_ancestors():
    steps = [m.StepResult(step_no=1, is_correct=False, kp="C")]
    # 祖先 A,B 均已掌握
    plan = m.plan_mastery_updates(
        steps, EDGES, current_mastery={"A": 0.9, "B": 0.9}
    )
    assert plan.updates["C"]["wrong"] == 1         # 该步知识点被扣
    assert "A" not in plan.updates                # 已掌握祖先不被污染
    assert "B" not in plan.updates
    assert plan.weak_prereqs == set()             # A,B 已掌握 -> 不标薄弱


# ---------------- 答错：回溯薄弱前置点 ---------------- #
def test_wrong_traces_weak_prereqs():
    steps = [m.StepResult(step_no=1, is_correct=False, kp="C")]
    # B 薄弱(0.3)，A 掌握(0.9)
    plan = m.plan_mastery_updates(
        steps, EDGES, current_mastery={"A": 0.9, "B": 0.3}
    )
    assert plan.updates["C"]["wrong"] == 1
    # B 是薄弱前置 -> 既被标记，又得到一次负向（根因加固）
    assert "B" in plan.updates and plan.updates["B"]["wrong"] == 1
    assert "A" not in plan.updates              # A 已掌握，不污染
    assert plan.weak_prereqs == {"B"}


# ---------------- 部分步错：多步多知识点，错步不污染已掌握祖先 ---------------- #
def test_partial_steps():
    steps = [
        m.StepResult(step_no=1, is_correct=True, kp="C"),   # 第1步对
        m.StepResult(step_no=2, is_correct=False, kp="B"),  # 第2步错
    ]
    plan = m.plan_mastery_updates(steps, EDGES, current_mastery={"A": 0.9})
    # 第1步(对, kp=C)：C/B/A 各 +1 正确
    assert plan.updates["C"]["correct"] == 1
    assert plan.updates["A"]["correct"] == 1
    # 第2步(错, kp=B)：B 额外 +1 错误；同时 B 已因 C 对而 +1 正确
    assert plan.updates["B"]["correct"] == 1
    assert plan.updates["B"]["wrong"] == 1
    # A 已掌握，不因 B 错而被扣
    assert plan.updates["A"]["wrong"] == 0
    assert "A" not in plan.weak_prereqs


# ---------------- 事件→步骤结果推导 ---------------- #
def test_wrong_step_nos_for_event():
    assert m.wrong_step_nos_for_event(None, 3, True) == set()          # 整题对
    assert m.wrong_step_nos_for_event(2, 3, False) == {2, 3}           # 首错步=2
    assert m.wrong_step_nos_for_event(None, 3, False) == {1, 2, 3}     # 整题错无首错步
    assert m.wrong_step_nos_for_event("abc", 3, False) == {1, 2, 3}    # 脏首错步按整题错


def test_derive_step_results():
    steps = [{"step_no": 1, "kp_code": "A"}, {"step_no": 2, "kp_code": "B"}]
    res = m.derive_step_results(steps, {2})
    assert res[0].is_correct is True and res[0].kp == "A"
    assert res[1].is_correct is False and res[1].kp == "B"


# ---------------- 遗忘衰减 ---------------- #
def test_decay_no_practice_is_none():
    assert m.compute_mastery_score(0, 0) is None


def test_decay_factor_boundaries():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert m.decay_factor(now, now) == 1.0
    assert m.decay_factor(None, now) == 1.0
    f = m.decay_factor(now, now + timedelta(days=30))
    assert 0 < f < 1.0


def test_decay_reduces_mastery_over_time():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fresh = m.compute_mastery_score(10, 0, last_practiced_at=now, now=now)
    later = m.compute_mastery_score(10, 0, last_practiced_at=now, now=now + timedelta(days=30))
    old = m.compute_mastery_score(10, 0, last_practiced_at=now, now=now + timedelta(days=60))
    assert fresh == 1.0
    assert later < fresh
    assert old < later


def test_decay_half_life():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 50% 对 50% 错，刚练完掌握度 0.5；放一个半衰期后应衰减到 0.5*0.5=0.25
    sc = m.compute_mastery_score(5, 5, last_practiced_at=now, now=now + timedelta(days=30))
    assert round(sc, 4) == 0.25


# ---------------- 直接调用入口（无 DB 的纯逻辑部分） ---------------- #
def test_update_mastery_plan_only():
    """update_mastery 的纯逻辑（plan 计算）通过 plan_mastery_updates 已覆盖；
    这里验证 _to_step_result 兼容 dict 输入。"""
    sr = [m._to_step_result({"step_no": 1, "is_correct": True, "kp": "C"})]
    assert sr[0] == m.StepResult(step_no=1, is_correct=True, kp="C")
    assert m._to_step_result("garbage") is None


# ---------------- DB 薄层：名称键兼容（假 session，不连库） ---------------- #
import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeSession:
    """两条路由：title 映射查询 / ORM select(student_mastery)。"""

    def __init__(self, mastery_rows, title_rows):
        self._routes = [
            ("SELECT title, code", title_rows),
            ("FROM student_mastery", mastery_rows),
        ]

    async def execute(self, stmt):
        sql = str(stmt)
        for marker, rows in self._routes:
            if marker in sql:
                return _FakeResult(rows)
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_load_mastery_map_code_keys_no_title_query():
    """code 键行原样匹配，且不触发 title 映射查询（无名称键时零额外开销）。"""
    sess = _FakeSession(
        [SimpleNamespace(knowledge_point="A", correct_count=9,
                         wrong_count=1, last_practiced_at=None)],
        title_rows=[("不该被查", "X")],
    )
    out = _run(m.load_mastery_map(sess, "stu", ["A", "B"]))
    assert out == {"A": 0.9}


def test_load_mastery_map_resolves_name_keys():
    """历史名称键行经 title 映射翻译成 code；翻译不到的名称忽略。"""
    sess = _FakeSession(
        [
            SimpleNamespace(knowledge_point="名称甲", correct_count=1,
                            wrong_count=1, last_practiced_at=None),      # -> K01, 0.5
            SimpleNamespace(knowledge_point="旧体系无对应", correct_count=5,
                            wrong_count=0, last_practiced_at=None),      # 忽略
        ],
        title_rows=[("名称甲", "K01")],
    )
    out = _run(m.load_mastery_map(sess, "stu", ["K01", "K02"]))
    assert out == {"K01": 0.5}


def test_load_mastery_map_skips_unjudged():
    """judged=0 不计掌握度（T-B2 约定：只练过没判分不构成掌握度）。"""
    sess = _FakeSession(
        [SimpleNamespace(knowledge_point="K01", correct_count=0,
                         wrong_count=0, last_practiced_at=None)],
        title_rows=[],
    )
    out = _run(m.load_mastery_map(sess, "stu", ["K01"]))
    assert out == {}
