"""T-F1 写侧收口：掌握度写入的知识点键必须统一为 DAG code。

对齐 web-backend 测试哲学（不连库）：用最小 FakeDB 按 SQL 文本分派，
只验证「键的翻译与校验」这一层语义，不牵扯事务细节。
"""
from typing import Dict, List

from app.services import mastery as m

KP = {
    "MA-B4-C1-S2-K02": "等差数列的通项公式",
    "MA-B4-C1-S2-K03": "等差数列的前 n 项和公式",
    "MA-B3-C3-S1-K03": "椭圆的几何性质",
}


class _Result:
    def __init__(self, rows: List):
        self._rows = rows

    def all(self):
        return self._rows


class FakeDB:
    """按 SQL 文本分派的最小 DB 打桩。忽略绑定参数：语义由调用方按 keys 收敛。"""

    def __init__(self, index: Dict[str, str] | None = None):
        self.index = index if index is not None else KP
        self.sqls: List[str] = []

    async def execute(self, stmt, *a, **k):
        # 用 literal_binds 编译：INSERT 的值会内联进 SQL 文本，便于断言落库的键
        try:
            from sqlalchemy.dialects import postgresql

            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        except Exception:  # noqa: BLE001
            sql = getattr(stmt, "text", None) or str(stmt)
        self.sqls.append(sql)
        if "kb_knowledge_points" in sql and "title" in sql:
            return _Result([(t, c) for c, t in self.index.items()])
        return _Result([(c,) for c in self.index])

    async def commit(self):
        return None


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ---------------- resolve_kp_codes ---------------- #

def test_code_passthrough():
    out = _run(m.resolve_kp_codes(FakeDB(), ["MA-B4-C1-S2-K02"]))
    assert out == {"MA-B4-C1-S2-K02": "MA-B4-C1-S2-K02"}


def test_title_translated_to_code():
    out = _run(m.resolve_kp_codes(FakeDB(), ["等差数列的通项公式"]))
    assert out == {"等差数列的通项公式": "MA-B4-C1-S2-K02"}


def test_unknown_key_resolves_to_none():
    """既非 code 也无对应 title -> None（调用方跳过，不写脏键）。"""
    out = _run(m.resolve_kp_codes(FakeDB(), ["某个不存在的知识点"]))
    assert out == {"某个不存在的知识点": None}


def test_mixed_keys():
    out = _run(
        m.resolve_kp_codes(
            FakeDB(),
            ["MA-B4-C1-S2-K02", "椭圆的几何性质", "查无此点"],
        )
    )
    assert out["MA-B4-C1-S2-K02"] == "MA-B4-C1-S2-K02"
    assert out["椭圆的几何性质"] == "MA-B3-C3-S1-K03"
    assert out["查无此点"] is None


def test_empty_input():
    assert _run(m.resolve_kp_codes(FakeDB(), [])) == {}


# ---------------- apply_mastery_updates 写侧收口 ---------------- #

def test_write_translates_title_to_code():
    db = FakeDB()
    _run(
        m.apply_mastery_updates(
            db, "s1", {"等差数列的通项公式": {"correct": 1}}, subject="math"
        )
    )
    inserts = " ".join(s for s in db.sqls if s.startswith("INSERT"))
    assert inserts, "应产生 INSERT"
    # 真正落库的键必须是 code，而不是中文名
    assert "MA-B4-C1-S2-K02" in inserts
    assert "等差数列的通项公式" not in inserts


def test_write_skips_unresolvable_key():
    """无法解析的键：跳过写入（不产生 INSERT），而不是把脏键写进表。"""
    db = FakeDB()
    _run(
        m.apply_mastery_updates(
            db,
            "s1",
            {"抛物线的焦点与准线": {"correct": 1}, "MA-B4-C1-S2-K02": {"correct": 2}},
            subject="math",
        )
    )
    inserts = " ".join(s for s in db.sqls if s.startswith("INSERT"))
    assert "MA-B4-C1-S2-K02" in inserts        # 可解析的正常写
    assert "抛物线的焦点与准线" not in inserts  # 不可解析的不写进表


def test_zero_delta_not_written():
    db = FakeDB()
    _run(m.apply_mastery_updates(db, "s1", {"MA-B4-C1-S2-K02": {"correct": 0, "wrong": 0}}))
    assert not any("INSERT" in s for s in db.sqls)
