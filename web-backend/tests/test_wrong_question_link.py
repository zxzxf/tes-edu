"""T2 错题本自动写入链路：判分出错 → wrong_questions（幂等 + 题干回填）。

测试均为纯逻辑（不连数据库），DB 交互用按调用顺序返回预设结果的假 session：
  第 1 次 execute = 去重查询 _find_wrong；第 2 次 execute（仅新插入且缺题干/知识点时）
  = course_kb 回填 _fetch_course_kb。
"""
import asyncio
from datetime import datetime, timezone

from app.models.course_kb import CourseQuestion
from app.models.ops import WrongQuestion
from app.services import learning


# ------------------------------------------------------------------ 夹具

def wrong_payload(**over) -> dict:
    """判分为错的 solve 事件（judge v2 载荷）。"""
    base = {
        "event_type": "solve",
        "student_id": "s1",
        "tenant_id": "t1",
        "subject": "数学",
        "question": "解方程 2x=4",
        "knowledge_points": ["一元二次不等式"],
        "content_hash": "h_single",
        "correct": False,
        "partial": False,
        "first_wrong_step": 1,
        "error_type": "calc",
        "attribution_source": "map",
        "evidence": "学生选 A（x=1）",
        "student_answer": "A",
        "ts": "2026-10-24T01:00:00+00:00",
    }
    base.update(over)
    return base


class _FakeResult:
    def __init__(self, obj=None):
        self._obj = obj

    def scalars(self):
        return self

    def first(self):
        return self._obj

    def all(self):
        return [self._obj] if self._obj is not None else []


class _FakeSession:
    """按调用顺序弹出预设查询结果；add/commit 同既有假 session 约定。"""

    def __init__(self, results=None):
        self.added = []
        self.committed = 0
        self.executed = 0
        self._results = list(results or [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        return obj

    async def execute(self, stmt):
        self.executed += 1
        return self._results.pop(0) if self._results else _FakeResult(None)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------- 纯函数：抽取判据

def test_extract_wrong_question_full():
    row = learning.extract_wrong_question(wrong_payload())
    assert row is not None
    assert row["student_id"] == "s1"
    assert row["content_hash"] == "h_single"
    assert row["wrong_step_no"] == 1
    assert row["error_type"] == "calc"
    assert row["attribution_source"] == "map"
    assert row["evidence"] == "学生选 A（x=1）"
    # wrong_answer 优先取学生原始作答，而不是证据片段
    assert row["wrong_answer"] == "A"


def test_extract_wrong_question_falls_back_to_evidence():
    row = learning.extract_wrong_question(wrong_payload(student_answer=None))
    assert row["wrong_answer"] == "学生选 A（x=1）"


def test_extract_truncates_long_kp():
    """knowledge_point 列是 String(128)，超长截断防插入失败。"""
    row = learning.extract_wrong_question(wrong_payload(knowledge_points=["知" * 200]))
    assert row["knowledge_point"] == "知" * 128


def test_extract_skips_when_not_wrong():
    """答对 / 未判分（None）/ 过程缺失都绝不能进错题本。"""
    assert learning.extract_wrong_question(wrong_payload(correct=True)) is None
    assert learning.extract_wrong_question(wrong_payload(correct=None)) is None
    # need_process=True 与 correct=False 同时出现（异常路径）也要拦住
    assert learning.extract_wrong_question(wrong_payload(need_process=True)) is None
    assert learning.extract_wrong_question("not a dict") is None
    assert learning.extract_wrong_question({}) is None


def test_extract_allows_partial_and_missing_hash():
    """部分步错（partial=True）也是错题；无 hash/无错因也记（归因是增强不是前提）。"""
    row = learning.extract_wrong_question(wrong_payload(partial=True))
    assert row is not None and row["wrong_step_no"] == 1
    row2 = learning.extract_wrong_question(
        wrong_payload(content_hash="", error_type=None, first_wrong_step=None)
    )
    assert row2 is not None
    assert row2["content_hash"] is None
    assert row2["error_type"] is None


# ------------------------------------------------- DB 层：插入 / 幂等 / 回填

def test_record_wrong_question_inserts():
    db = _FakeSession()  # 去重查询无命中
    ok = _run(learning.record_wrong_question(db, wrong_payload()))
    assert ok is True
    assert len(db.added) == 1
    w = db.added[0]
    assert isinstance(w, WrongQuestion)
    assert w.student_id == "s1"
    assert w.question == "解方程 2x=4"
    assert w.wrong_answer == "A"
    assert w.knowledge_point == "一元二次不等式"
    assert w.content_hash == "h_single"
    assert w.wrong_step_no == 1
    assert w.error_type == "calc"
    assert w.attribution_source == "map"
    assert w.created_at is not None
    assert db.committed >= 1


def test_record_wrong_question_skips_non_wrong():
    db = _FakeSession()
    assert _run(learning.record_wrong_question(db, wrong_payload(correct=True))) is False
    assert _run(learning.record_wrong_question(db, wrong_payload(need_process=True))) is False
    assert db.added == []
    assert db.executed == 0


def test_record_wrong_question_idempotent():
    """重复消费同一事件：不插新行，只刷新 created_at 与归因（Kafka at-least-once）。"""
    old = datetime(2026, 10, 20, tzinfo=timezone.utc)
    existing = WrongQuestion(
        id=7, student_id="s1", question="解方程 2x=4",
        content_hash="h_single", wrong_step_no=1,
        error_type=None, evidence=None, created_at=old,
    )
    db = _FakeSession([_FakeResult(existing)])
    ok = _run(learning.record_wrong_question(db, wrong_payload()))
    assert ok is False
    assert db.added == []                      # 条数不增长
    assert existing.created_at > old           # 时间被刷新
    assert existing.error_type == "calc"       # 归因被补全
    assert db.committed == 1


def test_record_wrong_question_backfills_stem_and_kp():
    """事件缺题干/知识点时，按 content_hash 从 course_kb 回填。"""
    kb = CourseQuestion(subject="数学", stem="KB 题干：2x=4", knowledge_points=["一元二次方程"])
    db = _FakeSession([_FakeResult(None), _FakeResult(kb)])
    ok = _run(
        learning.record_wrong_question(
            db, wrong_payload(question=None, knowledge_points=[])
        )
    )
    assert ok is True
    assert db.executed == 2                    # 去重 + 回填各一次查询
    w = db.added[0]
    assert w.question == "KB 题干：2x=4"
    assert w.knowledge_point == "一元二次方程"


def test_record_wrong_question_without_hash_still_records():
    """hash 缺失也入库（correction 为 null，读取端容忍）；题干取事件自带。"""
    db = _FakeSession()
    ok = _run(learning.record_wrong_question(db, wrong_payload(content_hash="")))
    assert ok is True
    assert db.executed == 1                    # 只做去重查询，不回查 course_kb
    assert db.added[0].content_hash is None
    assert db.added[0].question == "解方程 2x=4"


def test_record_wrong_question_placeholder_when_no_stem():
    """题干与 hash 全缺（NOT NULL 列）：占位文案保证可追溯。"""
    db = _FakeSession()
    ok = _run(
        learning.record_wrong_question(db, wrong_payload(question=None, content_hash=""))
    )
    assert ok is True
    assert "题干待补全" in db.added[0].question
