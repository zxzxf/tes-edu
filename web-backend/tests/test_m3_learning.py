"""M3-1/2/3/4/5 学习闭环：解析幂等与锁、知识点绑定、按卷图谱、同条件推题。"""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import exam, exam_learning
from app.models.course_kb import Base, CourseQuestion
from app.models.ops import (
    ExamAssignment, ExamPaper, ExamPaperQuestion, ExamSolution, ExamSubmission,
)
from app.models.schemas import ManualPaperCreate, ManualPaperItem
from app.services import question_kp, similar_kp, solution


def _engine():
    """sqlite 内存库 + 补 PG 专用函数（btrim）。

    线上是 PostgreSQL；`resolve_kp_codes` 用 btrim 去掉前后空白。sqlite 没有这个
    函数，给它注册一个等价实现，这样测试跑的是**真实代码路径**而不是替身。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_function("btrim", 1, lambda s: s.strip() if isinstance(s, str) else s)

    return engine


async def _bootstrap(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # kb_knowledge_points 由 ORM 建出；kb_kp_prerequisites 只有 SQL 脚本，
        # 这里补一张最小的边表（线上由 知识点本体/kb_kp_extend.sql 建）。
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS kb_kp_prerequisites (kp_code TEXT, prereq_code TEXT, "
            "PRIMARY KEY (kp_code, prereq_code))"
        ))
        # ORM 只映射了 8 列，DAG 脚本补的 code/chapter/... 线上存在、create_all 不会建
        for col in ("code", "chapter", "section", "importance", "understanding",
                    "memory", "analogy"):
            try:
                await conn.execute(text(f"ALTER TABLE kb_knowledge_points ADD COLUMN {col} TEXT"))
            except Exception:
                pass
        await conn.execute(text(
            "INSERT INTO kb_knowledge_points (id, subject, title, content, code) VALUES "
            "(1, '数学', '等比数列', '...', 'MA-K1'), "
            "(2, '数学', '等比中项', '...', 'MA-K2'), "
            "(3, '数学', '数列求和', '...', 'MA-K3')"
        ))
        await conn.execute(text(
            "INSERT INTO kb_kp_prerequisites (kp_code, prereq_code) VALUES "
            "('MA-K2', 'MA-K1'), ('MA-K3', 'MA-K2')"
        ))


def _paper(db, *, qtype="choice", answer="A", kps=None, seq=1, content_hash="h-1"):
    paper = ExamPaper(tenant_id="school-1", title="卷", paper_type="pure_choice",
                      grade="高三", subject="数学", published=True)
    db.add(paper)
    return paper


async def _make_paper(db, *, stem="已知等比数列，求公比", answer="±2", kps=("MA-K1",)):
    paper = ExamPaper(tenant_id="school-1", title="卷", paper_type="pure_blank",
                      grade="高三", subject="数学", published=True)
    db.add(paper)
    await db.flush()
    q = ExamPaperQuestion(
        paper_id=paper.id, seq=1, qtype="blank", multi=False, stem=stem,
        answer=answer, score=5, knowledge_points=list(kps), content_hash="p-1",
    )
    db.add(q)
    assign = ExamAssignment(paper_id=paper.id, tenant_id="school-1",
                            target_type="grade", target_grade="高三")
    db.add(assign)
    await db.flush()
    return paper, q, assign


# ---------------------------------------------------------------- M3-2 解析锁
def test_solution_locked_before_submit():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db)
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await exam_learning.student_solution(
                    assign.id, 1,
                    user={"user_id": "stu-1", "tenant_id": "school-1"}, db=db,
                )
            assert exc.value.status_code == 403
            assert exc.value.detail["code"] == "SOLUTION_LOCKED"
        await engine.dispose()

    asyncio.run(scenario())


def test_solution_after_submit_is_idempotent():
    """M3-1：首次生成并落库；二次直接返回缓存，不重复调模型。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db)
            db.add(ExamSubmission(assignment_id=assign.id, paper_id=paper.id,
                                  student_id="stu-1", tenant_id="school-1"))
            await db.commit()

            calls = {"n": 0}

            async def fake_llm(_db, _tenant, _q):
                calls["n"] += 1
                return {"answer": "±2", "steps": ["由 S4=4、S8=68 解出 q", "q=±2"]}

            old = solution._call_llm
            solution._call_llm = fake_llm
            try:
                first = await solution.get_or_generate(
                    db, tenant_id="school-1", paper_id=paper.id, question=q)
                await db.commit()
                second = await solution.get_or_generate(
                    db, tenant_id="school-1", paper_id=paper.id, question=q)
                assert calls["n"] == 1
                assert first["cached"] is False and first["ok"] is True
                assert second["cached"] is True
                assert second["solution_steps"]["steps"][-1] == "q=±2"
            finally:
                solution._call_llm = old
        await engine.dispose()

    asyncio.run(scenario())


def test_solution_numeric_conflict_is_not_cached():
    """M2-11：解析结论与标答冲突 -> 不入缓存（否则错误解析永久固化）。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db, answer="2")
            await db.commit()

            async def fake_llm(_db, _tenant, _q):
                return {"answer": "3", "steps": ["算得 3"]}

            old = solution._call_llm
            solution._call_llm = fake_llm
            try:
                out = await solution.get_or_generate(
                    db, tenant_id="school-1", paper_id=paper.id, question=q)
                assert out["ok"] is False and out.get("degraded") is True
                assert out["conflicts"]
                assert await solution.get_cached(db, paper.id, q.id) is None
            finally:
                solution._call_llm = old
        await engine.dispose()

    asyncio.run(scenario())


def test_solution_concurrent_insert_returns_cached_row():
    """M3-1 防刷：两个请求同时生成时，后到者撞唯一键应回缓存，而不是 500。

    模拟方式：本请求调 LLM 期间，另一请求已把同一 (paper_id, question_id) 落库。
    断言不抛 IntegrityError、返回 cached=True，且不会二次插入。
    """
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db)
            await db.commit()

            async def racing_llm(_db, _tenant, _q):
                # 另一个并发请求抢先写入同一缓存键
                async with factory() as other:
                    other.add(ExamSolution(
                        tenant_id="school-1", paper_id=paper.id, paper_question_id=q.id,
                        solution_steps={"answer": "±2", "steps": ["并发先到"]},
                        prompt_version=solution.SOLUTION_PROMPT_VERSION, model="race",
                    ))
                    await other.commit()
                return {"answer": "±2", "steps": ["本请求后到"]}

            old = solution._call_llm
            solution._call_llm = racing_llm
            try:
                out = await solution.get_or_generate(
                    db, tenant_id="school-1", paper_id=paper.id, question=q)
            finally:
                solution._call_llm = old
            assert out["cached"] is True
            assert out["solution_steps"]["steps"] == ["并发先到"]
            rows = (await db.execute(
                select(ExamSolution).where(ExamSolution.paper_id == paper.id)
            )).scalars().all()
            assert len(rows) == 1
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M3-3 绑定
def test_bind_kp_creates_two_rows_for_two_kps():
    """卡面验收：绑 1 题到 2 个 kp -> question_kp 2 行。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(CourseQuestion(subject="数学", grade="高三", type="单选", stem="题",
                                  answer="A", content_hash="h-1", tenant_id="school-1",
                                  status="published", knowledge_points=["MA-K1"]))
            await db.commit()
            qid = (await db.execute(select(CourseQuestion.id))).scalar()
            out = await question_kp.bind_kp_codes(
                db, tenant_id="school-1", question_ids=[qid],
                kp_codes=["MA-K1", "MA-K2"],
            )
            assert out["created"] == 2
            await db.commit()
            rows = (await db.execute(text("SELECT kp_code FROM question_kp"))).all()
            assert sorted(r[0] for r in rows) == ["MA-K1", "MA-K2"]
        await engine.dispose()

    asyncio.run(scenario())


def test_bind_kp_rejects_unknown_names():
    """名称翻不成 code -> 不写库（不造第二套键）。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(CourseQuestion(subject="数学", grade="高三", type="单选", stem="题",
                                  answer="A", content_hash="h-1", tenant_id="school-1",
                                  status="published"))
            await db.commit()
            qid = (await db.execute(select(CourseQuestion.id))).scalar()
            out = await question_kp.bind_kp_codes(
                db, tenant_id="school-1", question_ids=[qid],
                kp_codes=["不存在的知识点"],
            )
            assert out["created"] == 0
            assert out["unresolved"] == ["不存在的知识点"]
        await engine.dispose()

    asyncio.run(scenario())


def test_bind_kp_by_title_translates_to_code():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add(CourseQuestion(subject="数学", grade="高三", type="单选", stem="题",
                                  answer="A", content_hash="h-1", tenant_id="school-1",
                                  status="published"))
            await db.commit()
            qid = (await db.execute(select(CourseQuestion.id))).scalar()
            out = await question_kp.bind_kp_codes(
                db, tenant_id="school-1", question_ids=[qid], kp_codes=["等比数列"],
            )
            assert out["created"] == 1
            row = (await db.execute(text("SELECT kp_code FROM question_kp"))).first()
            assert row[0] == "MA-K1"
        await engine.dispose()

    asyncio.run(scenario())


def test_coverage_lists_unbound_questions():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            for i in (1, 2):
                db.add(CourseQuestion(subject="数学", grade="高三", type="单选", stem=f"题{i}",
                                      answer="A", content_hash=f"h-{i}", tenant_id="school-1",
                                      status="published"))
            await db.commit()
            ids = [r[0] for r in (await db.execute(select(CourseQuestion.id))).all()]
            await question_kp.bind_kp_codes(
                db, tenant_id="school-1", question_ids=[ids[0]], kp_codes=["MA-K1"])
            await db.commit()
            out = await question_kp.coverage(db, "school-1")
            assert out["total_questions"] == 2
            assert out["bound_questions"] == 1
            assert out["unbound_count"] == 1
            assert out["by_kp"][0]["kp_code"] == "MA-K1"
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M3-4 图谱
def test_paper_graph_marks_core_prereq_and_successor():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db, kps=("MA-K2",))
            await db.commit()
            out = await __import__("app.services.graph", fromlist=["x"]).paper_graph_db(
                db, paper.id)
            by_code = {n["code"]: n for n in out["nodes"]}
            assert by_code["MA-K2"]["in_paper"] is True
            assert by_code["MA-K1"]["is_prereq"] is True
            assert by_code["MA-K3"]["is_successor"] is True
            assert out["paper_kp_count"] == 1
        await engine.dispose()

    asyncio.run(scenario())


# ---------------------------------------------------------------- M3-5 推题
def test_similar_excludes_extra_conditions():
    """题库题多一个「各项均为正数」-> 必须剔除（标答会从 ±2 变成 2）。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, assign = await _make_paper(db, kps=("MA-K1",))
            db.add(CourseQuestion(subject="数学", grade="高三", type="填空",
                                  stem="已知等比数列，求公比", answer="±2",
                                  content_hash="ok", tenant_id="school-1", status="published",
                                  knowledge_points=["MA-K1"]))
            db.add(CourseQuestion(subject="数学", grade="高三", type="填空",
                                  stem="已知各项均为正数的等比数列，求公比", answer="2",
                                  content_hash="bad", tenant_id="school-1", status="published",
                                  knowledge_points=["MA-K1"]))
            await db.commit()
            out = await similar_kp.similar_same_conditions(
                db, tenant_id="school-1", paper_question=q, n=5)
            assert [i["content_hash"] for i in out["items"]] == ["ok"]
            assert any(e["content_hash"] == "bad" for e in out["excluded"])
        await engine.dispose()

    asyncio.run(scenario())


def test_extra_conditions_matches_agent_backend_wordlist():
    """两边条件词表必须一致（改一处忘另一处会让推题与判分口径分叉）。"""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "agent-backend" / "app" / "core" / "judge_v2.py"
    text_src = src.read_text(encoding="utf-8")
    m = re.search(r"_CONDITION_WORDS = \((.*?)\)", text_src, re.S)
    assert m, "agent-backend 条件词表结构变了，需要同步更新本测试"
    agent_words = tuple(re.findall(r'"([^"]+)"', m.group(1)))
    assert set(agent_words) == set(similar_kp.CONDITION_WORDS)

# ---------------------------------------------------------------- M3-9 错题重做

from app.models.ops import DrillCard, WrongQuestion  # noqa: E402
from app.services import drill_link, wrongbook  # noqa: E402


async def _redo_setup(db, *, qtype="choice", answer="A", options=None, multi=False,
                      content_hash="rh-1", with_card=True):
    """建 1 卷 + 1 题判错错题行（source=exam，带来源卷指针）。"""
    paper = ExamPaper(tenant_id="school-1", title="重做卷",
                      paper_type="pure_choice" if qtype == "choice" else "pure_blank",
                      grade="高三", subject="数学", published=True)
    db.add(paper)
    await db.flush()
    q = ExamPaperQuestion(
        paper_id=paper.id, seq=1, qtype=qtype, multi=multi, stem="重做题干",
        answer=answer, score=5, options=options, knowledge_points=["MA-K1"],
        content_hash=content_hash,
    )
    db.add(q)
    await db.flush()
    w = WrongQuestion(student_id="s1", question="重做题干", wrong_answer="B",
                      correct_answer=answer, knowledge_point="等比数列",
                      content_hash=content_hash, paper_id=paper.id,
                      paper_question_id=q.id, source="exam")
    db.add(w)
    await db.flush()
    card = None
    if with_card:
        card = await drill_link.ensure_card_for_kp(
            db, student_id="s1", tenant_id="school-1",
            kp_code="MA-K1", kp_name="等比数列",
        )
    return paper, q, w, card


def test_redo_choice_correct_resolves_and_pushes_card():
    """重做判对 → 错题行软删（tombstone 移出）+ 背诵卡 due_at 后推、ease 不动。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, w, card = await _redo_setup(db, qtype="choice", answer="A")
            before_due, before_ease = card.due_at, card.ease
            out = await wrongbook.redo_wrong(db, "s1", w.id, answer="A")
            assert out["correct"] is True and out["resolved"] is True
            assert out["card_pushed"] is True
            row = (await db.execute(select(WrongQuestion))).scalars().first()
            assert row.deleted_at is not None          # 软删 → 同步 tombstone 移出错题本
            fresh = (await db.execute(select(DrillCard))).scalars().first()
            assert fresh.ease == before_ease            # SM-2 参数不动
            assert fresh.due_at is not None and fresh.due_at > before_due
        await engine.dispose()

    asyncio.run(scenario())


def test_redo_choice_wrong_stays_and_updates_answer():
    """重做判错 → 留在错题本，最近一次作答被更新，不软删。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, w, card = await _redo_setup(db, qtype="choice", answer="A")
            out = await wrongbook.redo_wrong(db, "s1", w.id, answer="C")
            assert out["correct"] is False and out["resolved"] is False
            assert out["card_pushed"] is False
            row = (await db.execute(select(WrongQuestion))).scalars().first()
            assert row.deleted_at is None
            assert row.wrong_answer == "C"
        await engine.dispose()

    asyncio.run(scenario())


def test_redo_blank_multiblank_mismatch_manual():
    """多空题空数不符 → manual_pending 转人工，绝不硬判错、不动错题行。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, w, card = await _redo_setup(db, qtype="blank", answer="1；2")
            out = await wrongbook.redo_wrong(db, "s1", w.id, answer="3")
            assert out["status"] == "manual_pending"
            assert out["correct"] is None and out["resolved"] is False
            row = (await db.execute(select(WrongQuestion))).scalars().first()
            assert row.deleted_at is None and row.wrong_answer == "B"
        await engine.dispose()

    asyncio.run(scenario())


def test_redo_unavailable_when_source_gone():
    """原卷题已不存在（无指针且 hash 对不上）→ unavailable，不报错。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            w = WrongQuestion(student_id="s1", question="孤儿错题", wrong_answer="B",
                              knowledge_point="等比数列", content_hash="ghost-hash",
                              source="exam")
            db.add(w)
            await db.flush()
            out = await wrongbook.redo_wrong(db, "s1", w.id, answer="A")
            assert out["status"] == "unavailable"
            assert out["resolved"] is False
        await engine.dispose()

    asyncio.run(scenario())


def test_redo_other_students_row_returns_none():
    """重做别人的错题 → None（API 层 404），不泄露不误删。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            paper, q, w, card = await _redo_setup(db, qtype="choice", answer="A")
            out = await wrongbook.redo_wrong(db, "s2", w.id, answer="A")
            assert out is None
        await engine.dispose()

    asyncio.run(scenario())


def test_wrong_list_filter_by_paper():
    """按卷筛选（M3-9）：paper_id 只回该卷的错题，其他卷不混入。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            p1 = ExamPaper(tenant_id="school-1", title="卷一", paper_type="pure_choice",
                           grade="高三", subject="数学", published=True)
            p2 = ExamPaper(tenant_id="school-1", title="卷二", paper_type="pure_choice",
                           grade="高三", subject="数学", published=True)
            db.add_all([p1, p2])
            await db.flush()
            db.add_all([
                WrongQuestion(student_id="s1", question="卷一的题", knowledge_point="等比数列",
                              paper_id=p1.id, source="exam"),
                WrongQuestion(student_id="s1", question="卷二的题", knowledge_point="数列求和",
                              paper_id=p2.id, source="exam"),
            ])
            await db.commit()
            items = await wrongbook.list_sync(db, "s1", paper_id=p1.id)
            assert len(items) == 1
            assert items[0]["paper_id"] == p1.id and items[0]["question"] == "卷一的题"
        await engine.dispose()

    asyncio.run(scenario())
