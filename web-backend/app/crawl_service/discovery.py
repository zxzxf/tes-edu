"""M5-7 课程源定时自主发现：自动发现候选课程/题库源（C 形态最小切口）。

本条只做"自动发现 + 待教师审核"，而非一上来放开自动采集与发布：

  ① 触发层  —— 复用既有 ``crawl_job``（``job_kind=discovery`` + 既有 cron）；
  ② 发现层  —— 检索约束 → 候选 URL（搜索引擎 API 可选，无密钥走可信域名种子
             + 站内链接探索兜底）→ 轻量抓取 + 启发式分类；
  ③ 治理闸门 —— 强制继承出站 SSRF 复查 / 版权白名单 / 启发式低价值丢弃；
  ④ 转交层  —— 通过闸门的候选自动建 ``course_source(auto_discovered,
             review_status=pending_review, status=paused)`` + 草稿 ``crawl_job``，
             教师 B 端审核确认后才真正 ``fetch→parse→intake``，绝不自动发布。

纯函数（``ssrf_safe`` / ``domain_allowed`` / ``classify_content`` /
``discover_candidates``）不连库、可单测；集成函数 ``run_discovery`` 由
``scheduler.run_job`` 在 ``job_kind=discovery`` 时调用。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crawl_service import fetcher as crawl_fetch
from app.models.ops import CourseSource, CrawlJob

log = logging.getLogger(__name__)

#: 启发式：正文像题库/教材的特征词（命中越多越像可抽题页面）
_QUESTION_HINTS = (
    "选择题", "填空题", "解答题", "计算题", "证明题", "应用题",
    "单选题", "多选题", "判断题", "习题", "练习题", "例题", "真题",
    "答案", "解析", "考点", "难度",
)
#: 题号 / 选项模式（如 "1." "（1）" "A."）
_QUESTION_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:\d+[\.\)]|[（(]\d+[)）]|[A-E][\.、])"
)
#: 可信教育域名种子（无搜索引擎 API 时兜底；必须为可解析且根路径允许 GET 的规范域名）。
#: 裸域 pep.com.cn 不解析、zxxk.com 根路径 405，故统一用 www. 规范主机名。
DEFAULT_SEED_DOMAINS = (
    "www.pep.com.cn",   # 人教社
    "www.zxxk.com",     # 学科网
    "gaokao.com",       # 高考网
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SCRIPT_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.S | re.I
)


# ----------------------------------------------------------- 纯函数（可单测）

def _strip_html(html: str) -> str:
    """极简 HTML 清洗：去 script/style + 标签 + 折叠空白（不依赖外部库）。"""
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text)


def ssrf_safe(url: str) -> Tuple[bool, Optional[str]]:
    """候选 URL 是否通过 SSRF 闸门（复用 fetcher 逐跳复查 + DNS 重绑定复查）。"""
    try:
        crawl_fetch._resolve_safe(url)
        return True, None
    except ValueError as exc:
        return False, str(exc)


def domain_allowed(url: str, allowlist: Sequence[str]) -> bool:
    """候选域名是否命中授权白名单（复用 scheduler 的匹配规则）。

    空白名单时默认放行（与既有合规行为一致：业务未提供授权清单前不放行任何
    新源会被卡死）。
    """
    if not allowlist:
        return True
    return any(
        _allowlist_matches(url, rule) for rule in allowlist
    )


def _allowlist_matches(uri: str, rule: str) -> bool:
    """域名/URL 前缀匹配（与 scheduler._allowlist_matches 同语义，避免跨模块耦合）。"""
    from app.crawl_service import scheduler as crawl_sched

    return crawl_sched._allowlist_matches(uri, rule)


def classify_content(html: str, query: Optional[str] = None) -> Tuple[str, float]:
    """启发式判断页面是否像「可抽题的课程/题库」资源。

    返回 ``(category, confidence)``：``category`` ∈ {question, textbook, video, web}；
    ``confidence`` ∈ [0,1]，低于阈值视为低价值候选，不建源。
    """
    text = _strip_html(html)
    hint_hits = sum(1 for h in _QUESTION_HINTS if h in text)
    pattern_hits = len(_QUESTION_PATTERN.findall(text))
    score = min(1.0, (hint_hits * 0.08) + (pattern_hits * 0.05))
    if score >= 0.4 and pattern_hits >= 3:
        category = "question"
    elif hint_hits >= 2:
        category = "textbook"
    elif ".mp4" in (html or "") or "video" in (query or "").lower():
        category = "video"
    else:
        category = "web"
    return category, round(score, 3)


def discover_candidates(
    query: str,
    *,
    search=None,
    seeds: Sequence[str] = DEFAULT_SEED_DOMAINS,
) -> List[str]:
    """生成初始候选 URL 列表（种子域名首页）。

    ``search`` 为可选搜索引擎客户端（配置了 API 走真实搜索，返回候选 URL 列表）；
    未配置时走可信域名种子兜底：每个种子域名首页作为初始探索入口，真实子页
    由 ``run_discovery`` 的站内链接探索阶段跟随。
    """
    candidates: List[str] = []
    q = (query or "").strip()
    if search is not None:
        try:
            found = search(q) or []
            candidates.extend(str(u).split("#")[0] for u in found if u)
        except Exception as exc:  # noqa: BLE001 搜索失败不致命，退化为种子
            log.warning("discovery search failed: %s", exc)
    for domain in seeds:
        candidates.append(f"https://{domain}/")
    # 去重保序
    seen: set = set()
    out: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _extract_same_domain_links(html: str, base: str, limit: int) -> List[str]:
    """从 HTML 提取与 base 同域的站内链接（去重，限前 ``limit`` 个）。"""
    base_host = (urlsplit(base).hostname or "").lower()
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.I)
    out: List[str] = []
    seen: set = set()
    for h in hrefs:
        h = h.strip()
        if h.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        u = urljoin(base, h).split("#")[0]
        host = (urlsplit(u).hostname or "").lower()
        if host != base_host or not u.startswith(("http://", "https://")):
            continue
        if u in seen or u == base:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------- 集成（连库）

def _discovery_allowlist() -> List[str]:
    return [
        item.strip()
        for item in settings.crawl_compliance_allowlist.split(",")
        if item.strip()
    ]


async def _create_pending_source(
    db: AsyncSession, job: CrawlJob, url: str, category: str
) -> CourseSource:
    """通过闸门后：建待审源 + 草稿采集任务（均 paused，等教师审核）。"""
    host = urlsplit(url).hostname or url
    src = CourseSource(
        tenant_id=job.tenant_id,
        name=f"[自动发现] {host} / {category}",
        source_type="video" if category == "video" else "url",
        uri=url,
        parser="web",
        status="paused",
        auto_discovered=True,
        review_status="pending_review",
        created_by=job.created_by,
    )
    db.add(src)
    await db.flush()
    draft = CrawlJob(
        tenant_id=job.tenant_id,
        source_id=src.id,
        name=f"[草稿] 待审源 {src.id} 采集",
        path="P2",
        schedule=None,
        budget_limit=10,
        spent=0,
        status="paused",
        job_kind="harvest",
        created_by=job.created_by,
    )
    db.add(draft)
    await db.flush()
    return src


async def run_discovery(
    db: AsyncSession,
    job: CrawlJob,
    *,
    run=None,
    fetch_fn=None,
    max_candidates: int = 20,
    max_per_domain: int = 5,
) -> Dict[str, Any]:
    """执行一次自主发现（由 ``scheduler.run_job`` 在 ``job_kind=discovery`` 时调用）。

    遍历种子 → 抓首页 → 站内链接探索 → 对每个候选过 SSRF/白名单闸门 →
    抓样本 + 启发式分类 → 高置信度者建待审源 + 草稿任务。``fetch_fn`` 可注入
    （测试用 fake fetch），默认 ``safe_fetch_url``。``run`` 为外层已建好的
    ``CrawlRun``，用于溯源（origin=discovery）。返回统计摘要。
    """
    fetch_fn = fetch_fn or crawl_fetch.safe_fetch_url
    query = (job.query or job.name or "").strip()
    allowlist = _discovery_allowlist()
    candidates = discover_candidates(query)

    discovered = created = rejected = 0
    created_urls: List[str] = []
    seen: set = set()

    for seed in candidates:
        if discovered >= max_candidates:
            break
        ok, _reason = ssrf_safe(seed)
        if not ok or not domain_allowed(seed, allowlist):
            rejected += 1
            continue
        try:
            html = await fetch_fn(seed, timeout=20)
        except Exception as exc:  # noqa: BLE001 单页失败不影响整体
            log.warning("discovery fetch failed %s: %s", seed, exc)
            rejected += 1
            continue
        # 评估种子页本身
        cat, conf = classify_content(html, query)
        if conf >= 0.4 and cat in ("question", "textbook"):
            if seed not in seen:
                seen.add(seed)
                await _create_pending_source(db, job, seed, cat)
                created_urls.append(seed)
                created += 1
                discovered += 1
        # 站内链接探索
        for link in _extract_same_domain_links(html, seed, max_per_domain):
            if discovered >= max_candidates:
                break
            lok, _lreason = ssrf_safe(link)
            if not lok or not domain_allowed(link, allowlist):
                rejected += 1
                continue
            try:
                lhtml = await fetch_fn(link, timeout=20)
            except Exception as exc:
                log.warning("discovery fetch failed %s: %s", link, exc)
                rejected += 1
                continue
            lcat, lconf = classify_content(lhtml, query)
            if lconf >= 0.4 and lcat in ("question", "textbook"):
                if link not in seen:
                    seen.add(link)
                    await _create_pending_source(db, job, link, lcat)
                    created_urls.append(link)
                    created += 1
                    discovered += 1
        await db.flush()

    if run is not None:
        run.fetched = discovered
        run.inserted = created
        run.error = (
            f"自主发现：候选通过闸门 {discovered} / 新建待审源 {created} "
            f"/ 拒绝 {rejected}"
        )
    return {
        "candidates": discovered,
        "created_sources": created,
        "rejected": rejected,
        "urls": created_urls,
    }


async def approve_source(
    db: AsyncSession, tenant_id: str, source_id: int
) -> Optional[CourseSource]:
    """教师审核通过：待审源转 active + 启用关联草稿采集任务。"""
    src = (
        await db.execute(
            select(CourseSource).where(
                CourseSource.id == source_id,
                CourseSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if src is None or not src.auto_discovered:
        return None
    src.review_status = "approved"
    src.status = "active"
    draft = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.source_id == source_id,
                CrawlJob.tenant_id == tenant_id,
                CrawlJob.job_kind == "harvest",
            )
        )
    ).scalar_one_or_none()
    if draft is not None:
        draft.status = "active"
    await db.flush()
    return src


async def reject_source(
    db: AsyncSession, tenant_id: str, source_id: int
) -> bool:
    """教师驳回：清理待审源及其草稿采集任务（不触碰任何已入库数据）。"""
    src = (
        await db.execute(
            select(CourseSource).where(
                CourseSource.id == source_id,
                CourseSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if src is None or not src.auto_discovered:
        return False
    # 先删关联草稿 job（子），再删待审源（父），避免 FK 依赖
    drafts = (
        await db.execute(
            select(CrawlJob).where(
                CrawlJob.source_id == source_id,
                CrawlJob.tenant_id == tenant_id,
            )
        )
    ).scalars().all()
    for d in drafts:
        await db.delete(d)
    await db.delete(src)
    await db.flush()
    return True
