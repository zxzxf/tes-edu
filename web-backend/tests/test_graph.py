"""T-B5 知识图谱可视化服务单测（纯逻辑 + 假 session，不连库）。

覆盖：
- 配色带阈值与 T-B2/T-B3 对齐（<0.6 weak、0.6~0.8 medium、>=0.8 strong）；
- 统计头汇总（各带计数 / ZPD 数 / 边数 / 最大深度）；
- 单节点装配（掌握度取整、练习统计透传、前置去重排序、缺省值）；
- DB 薄层装配（假 session 按调用序喂数据）：排序、ZPD 阻塞传递、
  跨学科悬挂边丢弃、空学科 note；
- 载荷边方向约定：from=前置、to=后置（学习流向）。
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import graph


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ------------------------------------------------------------------ 纯函数

def test_mastery_band_boundaries():
    """阈值边界与 T-B2 对齐：0.6 进 medium、0.8 进 strong。"""
    assert graph.mastery_band(None) == "none"
    assert graph.mastery_band(0.0) == "weak"
    assert graph.mastery_band(0.59) == "weak"
    assert graph.mastery_band(0.6) == "medium"
    assert graph.mastery_band(0.79) == "medium"
    assert graph.mastery_band(0.8) == "strong"
    assert graph.mastery_band(1.0) == "strong"


def test_summarize_counts():
    nodes = [
        {"band": "strong", "zpd": False, "depth": 0},
        {"band": "weak", "zpd": True, "depth": 1},
        {"band": "none", "zpd": True, "depth": 2},
        {"band": "medium", "zpd": False, "depth": 2},
    ]
    s = graph.summarize(nodes, [("B", "A"), ("C", "B")])
    assert s["total"] == 4
    assert s["mastered"] == 1
    assert s["weak"] == 1
    assert s["medium"] == 1
    assert s["untouched"] == 1
    assert s["in_zpd"] == 2
    assert s["edges"] == 2
    assert s["max_depth"] == 2


def test_summarize_empty():
    s = graph.summarize([], [])
    assert s == {
        "total": 0, "mastered": 0, "weak": 0, "medium": 0,
        "untouched": 0, "in_zpd": 0, "edges": 0, "max_depth": 0,
    }


def test_assemble_node_defaults_and_rounding():
    """无练习记录 -> 计数归零、band=none；掌握度取 4 位；前置去重排序。"""
    n = graph.assemble_node(
        meta={"code": "K02", "title": "点", "chapter": "章", "section": "节",
              "subject": "数学", "importance": "core"},
        mastery_score=0.75555,
        practice=None,
        in_zpd=True,
        depth=1,
        question_count=7,
        prereqs=["K01", "K01", "", None, "K00"],
    )
    assert n["code"] == "K02"
    assert n["mastery"] == 0.7556
    assert n["band"] == "medium"
    assert n["zpd"] is True
    assert n["depth"] == 1
    assert n["question_count"] == 7
    assert n["prereqs"] == ["K00", "K01"]          # 去重 + 去空 + 排序
    assert n["practice_count"] == 0
    assert n["correct_count"] == 0
    assert n["wrong_count"] == 0
    assert n["last_practiced_at"] is None


def test_assemble_node_with_practice():
    last = datetime(2026, 10, 20, tzinfo=timezone.utc)
    n = graph.assemble_node(
        meta={"code": "K01"},
        mastery_score=0.5,
        practice={"practice_count": 4, "correct_count": 2,
                  "wrong_count": 2, "last_practiced_at": last},
        in_zpd=False,
        depth=0,
        question_count=0,
        prereqs=[],
    )
    assert n["mastery"] == 0.5
    assert n["band"] == "weak"
    assert n["practice_count"] == 4
    assert n["last_practiced_at"] == last


# ------------------------------------------------------------------ DB 薄层（假 session）

class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _FakeSession:
    """按 SQL 特征串路由预制结果（不依赖调用顺序，适配名称翻译新增的查询）。

    特征串覆盖：meta(importance) / edges(kb_kp_prerequisites) /
    title_map(SELECT title, code) / qcounts(count DISTINCT) /
    practice(文本查询带计数列) / mastery(ORM select)。
    """

    def __init__(self, meta=None, edges=None, mastery_rows=None, qcounts=None,
                 practice=None, title_map=None):
        self._routes = [
            ("importance", meta or []),
            ("kb_kp_prerequisites", edges or []),
            ("SELECT title, code", title_map or []),
            ("count(DISTINCT s.content_hash)", qcounts or []),
            ("practice_count, correct_count", practice or []),
            ("FROM student_mastery", mastery_rows or []),
        ]

    async def execute(self, stmt):
        sql = str(stmt)
        for marker, rows in self._routes:
            if marker in sql:
                return _FakeResult(rows)
        raise AssertionError(f"unexpected SQL: {sql[:100]}")


def _meta(*codes):
    return [
        (c, f"标题-{c}", "章节", "小节", "数学", "std") for c in codes
    ]


def test_knowledge_graph_db_assembly():
    """三节点链 A<-B<-C：A 已掌握不推、B 薄弱入 ZPD、C 被 B 阻塞不入。"""
    sess = _FakeSession(
        meta=_meta("A", "B", "C"),
        edges=[("B", "A"), ("C", "B")],
        mastery_rows=[
            SimpleNamespace(knowledge_point="A", correct_count=9,
                            wrong_count=1, last_practiced_at=None),   # 0.9 已掌握
            SimpleNamespace(knowledge_point="B", correct_count=1,
                            wrong_count=1, last_practiced_at=None),   # 0.5 薄弱
        ],
        qcounts=[("A", 3), ("B", 5)],
        practice=[("B", 4, 2, 2, None)],
    )
    out = _run(graph.knowledge_graph_db(sess, "stu-1"))

    assert out["student_id"] == "stu-1"
    assert [n["code"] for n in out["nodes"]] == ["A", "B", "C"]   # 按章节带内深度排序
    a, b, c = out["nodes"]
    assert (a["depth"], a["band"], a["zpd"]) == (0, "strong", False)
    assert a["question_count"] == 3
    assert (b["depth"], b["band"], b["zpd"]) == (1, "weak", True)
    assert b["practice_count"] == 4 and b["correct_count"] == 2
    assert (c["depth"], c["band"], c["zpd"]) == (2, "none", False)  # 前置 B 薄弱 -> 阻塞
    assert c["question_count"] == 0
    # 边方向：from=前置 -> to=后置
    assert out["edges"] == [{"from": "A", "to": "B"}, {"from": "B", "to": "C"}]
    assert out["summary"]["in_zpd"] == 1
    assert out["summary"]["mastered"] == 1
    assert out["summary"]["max_depth"] == 2


def test_knowledge_graph_db_resolves_name_keyed_mastery():
    """历史数据按 KP 名称落库：名称经 title 映射翻译成 code 后叠到节点。"""
    sess = _FakeSession(
        meta=_meta("K01", "K02"),
        edges=[],
        # 名称键：名称甲 -> K01；名称乙不在 KP 表（旧体系，忽略）；K02 无记录
        mastery_rows=[
            SimpleNamespace(knowledge_point="名称甲", correct_count=1,
                            wrong_count=1, last_practiced_at=None),   # 0.5
        ],
        qcounts=[],
        practice=[("名称甲", 3, 1, 2, None)],
        title_map=[("名称甲", "K01")],
    )
    out = _run(graph.knowledge_graph_db(sess, "stu-1"))
    k01 = next(n for n in out["nodes"] if n["code"] == "K01")
    k02 = next(n for n in out["nodes"] if n["code"] == "K02")
    assert k01["mastery"] == 0.5 and k01["band"] == "weak"
    assert k01["practice_count"] == 3 and k01["correct_count"] == 1
    assert k02["mastery"] is None and k02["band"] == "none"
    assert out["summary"]["weak"] == 1


def test_knowledge_graph_db_drops_dangling_edges():
    """跨学科悬挂边（Z 不在节点集）必须丢弃，不参与深度/ZPD。"""
    sess = _FakeSession(
        meta=_meta("A", "B"),
        edges=[("B", "A"), ("Z", "A")],       # Z 不在 meta 里
        mastery_rows=[],
        qcounts=[],
        practice=[],
    )
    out = _run(graph.knowledge_graph_db(sess, "stu-1"))
    assert out["edges"] == [{"from": "A", "to": "B"}]
    assert out["summary"]["edges"] == 1
    # 全无记录：A、B 都在 ZPD（A 无前置就绪；B 前置无记录按就绪）
    assert {n["code"] for n in out["nodes"] if n["zpd"]} == {"A", "B"}
    assert out["summary"]["in_zpd"] == 2


def test_knowledge_graph_db_empty():
    """空学科返回 note + 空结构，不炸。"""
    sess = _FakeSession(meta=[], edges=[], mastery_rows=[], qcounts=[], practice=[])
    out = _run(graph.knowledge_graph_db(sess, "stu-1", subject="物理"))
    assert out["nodes"] == [] and out["edges"] == []
    assert out["summary"]["total"] == 0
    assert "无知识点数据" in out["note"]
