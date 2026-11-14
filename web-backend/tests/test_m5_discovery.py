"""M5-7 课程源定时自主发现：自动发现候选源 + 待教师审核。

覆盖：
  * 纯函数（网络隔离）：ssrf_safe / domain_allowed / classify_content / discover_candidates；
  * 服务层：discovery 任务 run_job 自动建待审源 + 草稿任务；
  * 审核：approve_source 转 active 并启用草稿任务；reject_source 清理；
  * API 模型：_job_out / _source_out 暴露 job_kind/query/auto_discovered/review_status；
  * 闸门：拒绝私网/未授权 URL。
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crawl_service import discovery as disc
from app.crawl_service import scheduler as crawl
from app.crawl_service import search_client as sc
from app.crawl_service.api import _job_out, _source_out
from app.models.course_kb import Base
from app.models.ops import CourseSource, CrawlJob, CrawlRun


def _engine():
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register(dbapi_conn, _record):
        dbapi_conn.create_function(
            "btrim", 1, lambda s: s.strip() if isinstance(s, str) else s
        )

    return engine


async def _bootstrap(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ----------------------------------------------------------- 纯函数（无网络）
def test_classify_content_categories():
    q = "选择题 填空题 解答题 1. 2+3= A.4 B.5 答案 解析 真题 考点"
    cat, conf = disc.classify_content(q)
    assert cat == "textbook" and conf >= 0.4  # 命中多个题面特征词

    empty = disc.classify_content("<html>欢迎访问本站</html>")
    assert empty[1] < 0.4  # 低价值，不建源

    web = disc.classify_content("关于我们 联系我们 版权声明 网站地图")
    assert web[0] == "web"


def test_discover_candidates_seeds_and_search():
    # 无搜索引擎：返回种子域名首页
    seeds = disc.discover_candidates("高中数学", seeds=("math.example.edu",))
    assert seeds == ["https://math.example.edu/"]

    # 有搜索引擎：合并搜索结果 + 种子，去重保序
    def search(q):
        return ["https://found.example/1", "https://math.example.edu/"]

    out = disc.discover_candidates("高中数学", search=search,
                                   seeds=("math.example.edu",))
    assert out[0] == "https://found.example/1"
    assert out.count("https://math.example.edu/") == 1  # 去重


# ----------------------------------------------------------- 搜索引擎客户端
def test_build_search_client_disabled_and_missing_key(monkeypatch):
    # 未启用 -> None（discovery 退化种子）
    monkeypatch.setattr(sc.settings, "search_api_enabled", False)
    assert sc.build_search_client() is None
    # 启用但无 key -> 仍 None
    monkeypatch.setattr(sc.settings, "search_api_enabled", True)
    monkeypatch.setattr(sc.settings, "search_api_key", "")
    assert sc.build_search_client() is None


def test_build_search_client_enabled(monkeypatch):
    monkeypatch.setattr(sc.settings, "search_api_enabled", True)
    monkeypatch.setattr(sc.settings, "search_api_key", "k-test")
    monkeypatch.setattr(sc.settings, "search_api_provider", "serper")
    monkeypatch.setattr(sc.settings, "search_api_url", "")
    monkeypatch.setattr(sc.settings, "search_api_max_results", 7)
    client = sc.build_search_client()
    assert client is not None
    assert client.provider == "serper"
    assert client.url == sc.DEFAULT_SERPER_URL
    assert client.max_results == 7


def test_search_client_parse_serper_and_dedup():
    payload = {
        "organic_results": [
            {"link": "https://a.example/paper?x=1#frag"},
            {"link": "https://b.example/quiz"},
            {"link": "https://a.example/paper?x=1#frag"},  # 重复
            {"link": ""},  # 空链接忽略
        ]
    }

    def fake_post(url, body, headers):
        assert headers.get("X-API-KEY") == "k"
        assert body["q"] == "高中数学"
        return payload

    client = sc.SearchClient("serper", "k", http_post=fake_post)
    urls = client.search("高中数学")
    assert urls == ["https://a.example/paper?x=1", "https://b.example/quiz"]


def test_search_client_http_error_returns_empty():
    def boom(url, body, headers):
        raise RuntimeError("network down")

    client = sc.SearchClient("serper", "k", http_post=boom)
    assert client.search("anything") == []


def test_discovery_uses_search_client(monkeypatch):
    """搜索 API 接入后：搜索结果 URL 会进入候选并被建为待审源。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="高中数学 真题", budget_limit=10,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            html = "选择题 填空题 解答题 答案 解析 真题 考点"
            import app.crawl_service.fetcher as fm

            async def fake_fetch(url, timeout=20):
                return html

            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)

            class FakeClient:
                def search(self, q):
                    return ["https://serp.result/paper-math-2024"]

            monkeypatch.setattr(disc, "build_search_client", lambda: FakeClient())

            run = await crawl.run_job(db, job)
            await db.commit()
            uris = (await db.execute(select(CourseSource.uri))).scalars().all()
            assert "https://serp.result/paper-math-2024" in uris
        await engine.dispose()

    asyncio.run(scenario())


def test_domain_allowed_respects_allowlist():
    assert disc.domain_allowed("http://x.com", []) is True  # 空白名单默认放行
    assert disc.domain_allowed("http://edu.example.com/math", ["example.com"]) is True
    assert disc.domain_allowed("http://other.com", ["example.com"]) is False


def test_ssrf_safe_blocks_private(monkeypatch):
    import app.crawl_service.fetcher as fm

    def fake_resolve(u):
        if "127.0.0.1" in u or "localhost" in u:
            raise ValueError("private address blocked")
        return None

    monkeypatch.setattr(fm, "_resolve_safe", fake_resolve)
    ok, reason = disc.ssrf_safe("http://127.0.0.1/admin")
    assert ok is False and "private" in (reason or "")
    ok2, _ = disc.ssrf_safe("http://public.example/")
    assert ok2 is True


# ----------------------------------------------------------- 集成（待审源 + 草稿任务）
def test_discovery_job_creates_pending_source_and_draft(monkeypatch):
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="晚10点自主发现", source_id=None,
                job_kind="discovery", query="高中数学 习题", schedule="0 22 * * *",
                budget_limit=20,
            )
            await db.commit()
            assert job.job_kind == "discovery"

            # 网络隔离：强制闸门通过 + fake 抓取返回含题 HTML
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            html = "选择题 填空题 解答题 1. 2+3= A.4 B.5 答案 解析 真题 考点"

            import app.crawl_service.fetcher as fm

            async def fake_fetch(url, timeout=20):
                return html

            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)

            run = await crawl.run_job(db, job, origin="manual")
            await db.commit()
            assert run.status == "ok"
            assert run.origin == "discovery"  # 发现任务一律 discovery 溯源

            srcs = (await db.execute(
                select(CourseSource).where(CourseSource.auto_discovered == True)  # noqa: E712
            )).scalars().all()
            assert len(srcs) >= 1
            assert all(s.review_status == "pending_review" and s.status == "paused"
                       for s in srcs)

            drafts = (await db.execute(
                select(CrawlJob).where(
                    CrawlJob.job_kind == "harvest",
                    CrawlJob.source_id == srcs[0].id,
                )
            )).scalars().all()
            assert len(drafts) == 1
            assert drafts[0].status == "paused"
        await engine.dispose()

    asyncio.run(scenario())


def test_create_job_rejects_bad_kind_and_forces_p2_for_discovery():
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            with pytest.raises(ValueError, match="job_kind"):
                await crawl.create_job(
                    db, tenant_id="school-1", name="bad", source_id=None,
                    job_kind="magic",
                )
            # discovery 任务可创建（path 对其无意义，仅透传）
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学",
            )
            await db.commit()
            assert job.job_kind == "discovery"
            assert job.query == "数学"
        await engine.dispose()

    asyncio.run(scenario())


def test_review_approve_activates_source_and_draft(monkeypatch):
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", budget_limit=10,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            html = "选择题 填空题 解答题 答案 解析 真题 考点"
            import app.crawl_service.fetcher as fm

            async def fake_fetch(url, timeout=20):
                return html

            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)
            await crawl.run_job(db, job)
            await db.commit()
            src = (await db.execute(
                select(CourseSource).where(CourseSource.auto_discovered == True)  # noqa: E712
            )).scalars().first()
            assert src is not None

            approved = await disc.approve_source(db, "school-1", src.id)
            await db.commit()
            assert approved.review_status == "approved"
            assert approved.status == "active"
            draft = (await db.execute(
                select(CrawlJob).where(CrawlJob.source_id == src.id)
            )).scalars().first()
            assert draft.status == "active"
        await engine.dispose()

    asyncio.run(scenario())


def test_review_reject_cleans_source_and_draft(monkeypatch):
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", budget_limit=10,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            html = "选择题 填空题 解答题 答案 解析 真题 考点"
            import app.crawl_service.fetcher as fm

            async def fake_fetch(url, timeout=20):
                return html

            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)
            await crawl.run_job(db, job)
            await db.commit()
            src = (await db.execute(
                select(CourseSource).where(CourseSource.auto_discovered == True)  # noqa: E712
            )).scalars().first()

            ok = await disc.reject_source(db, "school-1", src.id)
            await db.commit()
            assert ok is True
            remaining = (await db.execute(
                select(CourseSource).where(CourseSource.id == src.id)
            )).scalars().first()
            assert remaining is None  # 待审源已清理
            drafts = (await db.execute(
                select(CrawlJob).where(CrawlJob.source_id == src.id)
            )).scalars().all()
            assert drafts == []  # 关联草稿任务也清理
        await engine.dispose()

    asyncio.run(scenario())


def test_discovery_rejects_unauthorized_candidate(monkeypatch):
    """闸门生效：未授权域名的候选不建源。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", schedule="0 22 * * *",
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            # 只允许 example.edu，种子里 pep/zxxk/gaokao 全被拒
            monkeypatch.setattr(disc, "domain_allowed",
                                lambda u, a: "example.edu" in u)
            html = "选择题 填空题 解答题 答案 解析 真题 考点"
            import app.crawl_service.fetcher as fm

            async def fake_fetch(url, timeout=20):
                return html

            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)
            run = await crawl.run_job(db, job)
            await db.commit()
            assert "拒绝" in (run.error or "")
            srcs = (await db.execute(
                select(CourseSource).where(CourseSource.auto_discovered == True)  # noqa: E712
            )).scalars().all()
            assert srcs == []
        await engine.dispose()

    asyncio.run(scenario())


# ----------------------------------------------------------- M5-7-8 递归探索 + headless
def test_discovery_recursive_crawl_depth(monkeypatch):
    """BFS 纵深：depth=1 只探首页直链；depth>=2 继续探下一层同域链接。"""
    async def scenario(depth: int) -> int:
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        fetched: list = []
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", budget_limit=50,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            # 固定单一种子，避免真实种子域干扰计数
            monkeypatch.setattr(
                disc, "discover_candidates",
                lambda q, **kw: ["https://math.example.edu/"],
            )
            monkeypatch.setattr(disc.settings, "discovery_crawl_depth", depth)
            monkeypatch.setattr(disc.settings, "discovery_crawl_max_pages", 100)

            # 三层页面：首页 -> /sub -> /sub2，每层含一个同域链接
            async def fake_fetch(url, timeout=20):
                fetched.append(url)
                if url.endswith("/sub2"):
                    return "<html>导航</html>"
                if url.endswith("/sub"):
                    return '<html><a href="/sub2">下一级</a>导航</html>'
                return '<html><a href="/sub">子页</a>导航</html>'

            import app.crawl_service.fetcher as fm
            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)

            run = CrawlRun(job_id=job.id, tenant_id=job.tenant_id,
                          status="running", origin="discovery")
            db.add(run)
            await db.flush()
            await disc.run_discovery(db, job, run=run)
            await db.commit()
            return len(fetched)
        await engine.dispose()

    got_d1 = asyncio.run(scenario(1))
    got_d2 = asyncio.run(scenario(2))
    # depth=1：首页 + /sub = 2 页；depth=2：再 + /sub2 = 3 页
    assert got_d1 == 2, got_d1
    assert got_d2 == 3, got_d2


def test_discovery_respects_page_budget(monkeypatch):
    """总页面预算 discovery_crawl_max_pages 必须掐住递归探索规模。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", budget_limit=50,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            monkeypatch.setattr(
                disc, "discover_candidates",
                lambda q, **kw: ["https://math.example.edu/"],
            )
            monkeypatch.setattr(disc.settings, "discovery_crawl_depth", 5)
            monkeypatch.setattr(disc.settings, "discovery_crawl_max_pages", 2)

            fetched: list = []

            async def fake_fetch(url, timeout=20):
                fetched.append(url)
                return '<html><a href="/x">x</a>导航</html>'

            import app.crawl_service.fetcher as fm
            monkeypatch.setattr(fm, "safe_fetch_url", fake_fetch)

            run = CrawlRun(job_id=job.id, tenant_id=job.tenant_id,
                          status="running", origin="discovery")
            db.add(run)
            await db.flush()
            await disc.run_discovery(db, job, run=run)
            await db.commit()
            return len(fetched)
        await engine.dispose()

    assert asyncio.run(scenario()) <= 2


def test_discovery_headless_render_path(monkeypatch):
    """headless 渲染注入后：经渲染拿到的 JS 正文被分类并建待审源，静态 fetch 不被调用。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="d", source_id=None,
                job_kind="discovery", query="数学", budget_limit=10,
            )
            await db.commit()
            monkeypatch.setattr(disc, "ssrf_safe", lambda u: (True, None))
            monkeypatch.setattr(disc, "domain_allowed", lambda u, a: True)
            monkeypatch.setattr(
                disc, "discover_candidates",
                lambda q, **kw: ["https://spa.example.edu/"],
            )
            monkeypatch.setattr(disc.settings, "discovery_crawl_depth", 0)  # 只看首页

            async def fake_render(url):
                # 模拟 JS 渲染后产出的真实题面（静态抓取拿不到）
                return "选择题 填空题 解答题 1. 2+3= A.4 B.5 答案 解析 真题 考点"

            async def fetch_must_not_run(url, timeout=20):
                raise AssertionError("headless 模式下不应走静态 fetch")

            run = CrawlRun(job_id=job.id, tenant_id=job.tenant_id,
                          status="running", origin="discovery")
            db.add(run)
            await db.flush()
            await disc.run_discovery(
                db, job, run=run, render_fn=fake_render, fetch_fn=fetch_must_not_run,
            )
            await db.commit()
            uris = (await db.execute(select(CourseSource.uri))).scalars().all()
            assert uris == ["https://spa.example.edu/"]
        await engine.dispose()

    asyncio.run(scenario())


# ----------------------------------------------------------- 定时触发（M5-7 调度闭环）
def test_nightly_cron_matches_22h_only():
    """'0 22 * * *' 只命中 22:00，不命中其它时刻（bootstrap 脚本默认 cron）。"""
    from datetime import datetime, timezone

    assert crawl.matches_cron("0 22 * * *", datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc))
    assert not crawl.matches_cron("0 22 * * *", datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc))
    assert not crawl.matches_cron("0 22 * * *", datetime(2026, 11, 13, 22, 5, tzinfo=timezone.utc))


def test_discovery_job_nightly_schedule_is_due_at_22h():
    """discovery 任务 + 夜间 cron 激活后：next_run_at 已算、到点被调度循环捞到。"""
    async def scenario():
        engine = _engine()
        await _bootstrap(engine)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            job = await crawl.create_job(
                db, tenant_id="school-1", name="自主发现｜数学",
                source_id=None, job_kind="discovery", query="数学",
                schedule="0 22 * * *",
            )
            # create_job 默认 paused（不算 next_run_at 也行，但应已算）
            assert job.next_run_at is not None
            await crawl.set_job_status(db, "school-1", job.id, "active")
            await db.commit()
            # 重新取一遍，确认 DB 里的 next_run_at 已落库
            jid = job.id
            persisted = (await db.execute(
                select(CrawlJob).where(CrawlJob.id == jid)
            )).scalar_one()
            assert persisted.status == "active"
            assert persisted.next_run_at is not None

            # 在 next_run_at 这一刻：应被 due_jobs 捞到（即会被定时执行）
            due_at_run = await crawl.due_jobs(db, now=persisted.next_run_at)
            assert any(j.id == jid for j in due_at_run)
            # 在非 22:00 的时刻：不应被捞到
            from datetime import datetime, timedelta, timezone
            off = persisted.next_run_at - timedelta(hours=1)
            due_off = await crawl.due_jobs(db, now=off)
            assert all(j.id != jid for j in due_off)
        await engine.dispose()

    asyncio.run(scenario())


def test_make_renderer_selection(monkeypatch):
    """渲染后端恒指向 headless-render 服务：未配 render_url 用默认别名，配了优先外部地址。

    137 上已部署 edu-headless-render、镜像不再带 chromium，实现改为无条件返回
    渲染器（discovery_headless_enabled 开关已废弃）；单测同步改为断言实际选用的
    服务地址，而不是期望 None。
    """
    seen = []

    async def _fake_render(url, svc, timeout):
        seen.append((url, svc, timeout))
        return "<html>ok</html>"

    monkeypatch.setattr(disc, "_render_via_service", _fake_render)
    monkeypatch.setattr(disc.settings, "discovery_render_url", "")
    renderer = disc._make_renderer()
    assert callable(renderer)
    asyncio.run(renderer("http://example.com/a"))
    assert seen[0][1] == disc.DEFAULT_RENDER_SERVICE

    monkeypatch.setattr(disc.settings, "discovery_render_url",
                        "http://render.internal/render")
    renderer = disc._make_renderer()
    assert callable(renderer)
    asyncio.run(renderer("http://example.com/b"))
    assert seen[1][1] == "http://render.internal/render"


def test_render_via_service_parses(monkeypatch):
    """外部渲染服务：JSON {"html":...} 与纯文本两种返回都能解析出 HTML。

    实现已改为 httpx 异步（不再用阻塞式 urllib），故这里 mock ``httpx.AsyncClient``。
    """
    import httpx

    class _FakeResp:
        def __init__(self, body: str):
            self._body = body

        def raise_for_status(self):
            return None

        @property
        def text(self):
            return self._body

    class _FakeAsyncClient:
        def __init__(self, payload: str):
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, svc, json=None):
            return _FakeResp(self._payload)

    # 情况一：JSON 包裹的 html
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient('{"html":"<html>选择题 答案 解析</html>"}'),
    )
    out = asyncio.run(disc._render_via_service("https://x/", "http://r/", 5))
    assert "<html>选择题" in out

    # 情况二：纯文本 HTML（无 JSON 包裹）
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: _FakeAsyncClient("<html>纯文本题面</html>"),
    )
    out2 = asyncio.run(disc._render_via_service("https://x/", "http://r/", 5))
    assert "<html>纯文本题面" in out2


# ----------------------------------------------------------- API 模型字段
def test_job_out_includes_kind_and_query():
    job = CrawlJob(
        id=1, tenant_id="school-1", name="d", job_kind="discovery",
        query="高中数学", path="P2", budget_limit=5, spent=0, status="paused",
    )
    out = _job_out(job)
    assert out.job_kind == "discovery" and out.query == "高中数学"


def test_source_out_includes_discovery_markers():
    src = CourseSource(
        id=1, tenant_id="school-1", name="x", source_type="url", uri="u",
        parser="web", status="paused", auto_discovered=True,
        review_status="pending_review",
    )
    out = _source_out(src)
    assert out.auto_discovered is True
    assert out.review_status == "pending_review"
