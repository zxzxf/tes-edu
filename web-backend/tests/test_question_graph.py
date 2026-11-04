"""M3-6 结果页逐题知识子图：题目标签解析 / 一层前驱后继 / 掌握度叠层 / 路由挂载。

三条红线：
 1. 子图**按单题锁定**，不能把整卷知识点混进来（那是 /graph 的职责）；
 2. 中文名要能翻译成 DAG code，翻不到的进 unresolved 而不是硬塞进图；
 3. 只展开一层前驱/后继，且悬挂边（端点不在子图内）必须丢弃。
"""
import asyncio
from types import SimpleNamespace

from app.services import graph
from main import app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


_META = {
    "A": {"code": "A", "title": "集合", "chapter": "章", "section": "节",
          "subject": "数学", "importance": "core"},
    "B": {"code": "B", "title": "复数", "chapter": "章", "section": "节",
          "subject": "数学", "importance": "std"},
    "C": {"code": "C", "title": "向量", "chapter": "章", "section": "节",
          "subject": "数学", "importance": "std"},
    "D": {"code": "D", "title": "后继点", "chapter": "章", "section": "节",
          "subject": "数学", "importance": "std"},
}
# (kp, prereq)：A -> B -> C -> D
_EDGES = [("B", "A"), ("C", "B"), ("D", "C"), ("D", "GHOST")]


# ------------------------------------------------------------------ 纯函数

def test_question_graph_plan_locks_to_single_question():
    """只给本题标签 C(+中文名「复数」=B)：core=BC，前驱 A、后继 D，远亲不进图。"""
    p = graph.question_graph_plan(["C", "复数", "查无此点"], _META, _EDGES)
    assert p["core"] == ["B", "C"]
    assert p["prereqs"] == ["A"]          # B 本身是 core，不再算作前驱
    assert p["successors"] == ["D"]
    assert p["nodes_set"] == {"A", "B", "C", "D"}
    assert p["unresolved"] == ["查无此点"]
    # GHOST 不在 meta 里 -> 悬挂边丢弃
    assert p["edges"] == [("A", "B"), ("B", "C"), ("C", "D")]


def test_question_graph_plan_empty_labels():
    """没绑知识点：返回空子图，调用方据此给「尚未标注」提示而不是白屏。"""
    p = graph.question_graph_plan([], _META, _EDGES)
    assert p["core"] == [] and p["nodes_set"] == set() and p["edges"] == []
    p2 = graph.question_graph_plan(["全是脏键"], _META, _EDGES)
    assert p2["nodes_set"] == set() and p2["unresolved"] == ["全是脏键"]


def test_question_graph_plan_one_hop_only():
    """本题是 A（链首）时，只补 B 作后继；C/D 是二级以上，不能滚进来。"""
    p = graph.question_graph_plan(["A"], _META, _EDGES)
    assert p["core"] == ["A"]
    assert p["prereqs"] == []
    assert p["successors"] == ["B"]
    assert p["nodes_set"] == {"A", "B"}


# ------------------------------------------------------------------ DB 薄层

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeSession:
    """按 SQL 特征串路由预制结果（不连库）。"""

    def __init__(self, meta=(), edges=(), bound=(), mastery_rows=(), title_map=()):
        self._routes = [
            ("question_kp", bound),
            ("importance", meta),
            ("kb_kp_prerequisites", edges),
            ("SELECT title, code", title_map),
            ("FROM student_mastery", mastery_rows),
        ]

    async def execute(self, stmt):
        sql = str(stmt)
        for marker, rows in self._routes:
            if marker in sql:
                return _FakeResult(rows)
        raise AssertionError(f"unexpected SQL: {sql[:120]}")


def _meta_rows():
    return [
        (m["code"], m["title"], m["chapter"], m["section"], m["subject"], m["importance"])
        for m in _META.values()
    ]


def test_question_graph_db_assembles_roles_depth_and_mastery():
    """卷面标签 + question_kp 标注取并集；节点分 core/前驱/后继三层，掌握度叠上去。"""
    sess = _FakeSession(
        meta=_meta_rows(),
        edges=_EDGES,
        bound=[("A",)],                    # 教师标注补的前置点
        mastery_rows=[
            SimpleNamespace(knowledge_point="C", correct_count=9, wrong_count=1,
                            last_practiced_at=None),
        ],
    )
    q = SimpleNamespace(knowledge_points=["C"], question_id=7, content_hash="h1")
    out = _run(graph.question_graph_db(
        sess, assignment_id=7, paper_id=9, seq=1, student_id="stu-1", question=q,
    ))

    assert out["assignment_id"] == 7 and out["paper_id"] == 9 and out["seq"] == 1
    # A 来自 question_kp 标注、C 来自卷面标签：两者都是本题考点
    assert out["core_kps"] == ["A", "C"]
    # B 既是 A 的后继又是 C 的前驱，三层角色互斥后只归到前驱层（不重复渲染）
    assert out["prereq_kps"] == ["B"]
    assert out["successor_kps"] == ["D"]
    assert out["unresolved_kps"] == []
    assert out["note"] == ""
    assert out["summary"]["total"] == 4
    roles = {n["code"]: (n["role"], n["depth"]) for n in out["nodes"]}
    assert roles == {
        "A": ("core", 1), "B": ("prereq", 0),
        "C": ("core", 1), "D": ("successor", 2),
    }
    c = next(n for n in out["nodes"] if n["code"] == "C")
    assert c["mastery"] is not None and c["band"] in {"weak", "medium", "strong"}
    assert out["edges"] == [
        {"from": "A", "to": "B"}, {"from": "B", "to": "C"}, {"from": "C", "to": "D"},
    ]


def test_question_graph_db_no_binding_gives_actionable_note():
    """一题都没绑知识点时不能返回空壳：note 要告诉学生/教师去标注。"""
    sess = _FakeSession(meta=_meta_rows(), edges=_EDGES)
    q = SimpleNamespace(knowledge_points=[], question_id=None, content_hash=None)
    out = _run(graph.question_graph_db(
        sess, assignment_id=7, paper_id=9, seq=2, student_id="stu-1", question=q,
    ))
    assert out["nodes"] == [] and out["core_kps"] == []
    assert "尚未绑定" in out["note"]
    assert out["summary"]["total"] == 0


def test_question_graph_route_mounted():
    """新路由必须挂进 app（学生端 /api/student/exams/.../graph），避免只在代码里存在。"""
    paths = {(r.path, tuple(sorted(getattr(r, "methods", set()) or {"GET"})))
             for r in app.routes}
    target = "/api/student/exams/{assignment_id}/questions/{seq}/graph"
    assert any(p == target and "GET" in methods for p, methods in paths), \
        f"missing route {target}"
