"""M5-7 搜索引擎客户端：给「自主发现」提供真实搜索结果 URL。

背景：仅靠可信域名种子抓首页，在 JS 重度站点（pep/zxxk/gaokao）拿不到题目文本，
导致 discovery 实际建不出源。接搜索 API 后，discovery 能按 ``job.query`` 关键词
真正去网上搜资源页 URL，再走既有 SSRF/白名单闸门 + 启发式分类。

默认实现 Serper（Google 结果聚合，返回 organic_results[].link）；其余兼容
provider 可后续扩展。HTTP 层可注入（测试用），默认 urllib。
未启用 / 缺失 key 时 ``search()`` 返回空列表，discovery 退化为种子兜底（向后兼容）。
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Callable, List, Optional

from app.core.config import settings

log = logging.getLogger(__name__)

DEFAULT_SERPER_URL = "https://google.serper.dev/search"

#: provider -> 默认端点
_PROVIDER_URLS = {
    "serper": DEFAULT_SERPER_URL,
}


def _extract_serper(payload: dict) -> List[str]:
    """从 Serper 响应抽取结果链接（organic_results[].link）。"""
    out: List[str] = []
    for item in (payload or {}).get("organic_results", []) or []:
        link = item.get("link")
        if link:
            out.append(link.split("#")[0])
    return out


#: provider -> 响应抽取函数
_PROVIDER_EXTRACT = {
    "serper": _extract_serper,
}


class SearchClient:
    """极简搜索客户端：``search(query) -> 结果 URL 列表（去重保序）``。"""

    def __init__(
        self,
        provider: str,
        api_key: str,
        *,
        url: Optional[str] = None,
        max_results: int = 10,
        http_post: Optional[Callable[[str, dict, dict], dict]] = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.url = url or _PROVIDER_URLS.get(provider, DEFAULT_SERPER_URL)
        self.max_results = max_results
        self._http_post = http_post or self._default_http_post
        self._extract = _PROVIDER_EXTRACT.get(provider, _extract_serper)

    def _default_http_post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def search(self, query: str) -> List[str]:
        if not self.api_key:
            return []
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key,
        }
        body = {"q": query, "num": self.max_results}
        try:
            payload = self._http_post(self.url, body, headers)
        except Exception as exc:  # noqa: BLE001 搜索失败不致命，退化为种子
            log.warning("search api failed (%s): %s", self.provider, exc)
            return []
        urls = self._extract(payload)
        seen: set = set()
        out: List[str] = []
        for u in urls:
            u = (u or "").split("#")[0]
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out[: self.max_results]


def build_search_client() -> Optional[SearchClient]:
    """按 settings 构建搜索客户端；未启用或缺失 key 返回 ``None``（discovery 退化种子）。"""
    if not getattr(settings, "search_api_enabled", False):
        return None
    key = getattr(settings, "search_api_key", "") or ""
    if not key:
        return None
    provider = getattr(settings, "search_api_provider", "serper") or "serper"
    url = getattr(settings, "search_api_url", "") or None
    max_results = getattr(settings, "search_api_max_results", 10) or 10
    return SearchClient(provider, key, url=url, max_results=max_results)
