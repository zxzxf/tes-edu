"""M5 采集源抓取与解析（让「采集任务」真正能采）。

此前 ``run_job`` 只接收调用方显式传入的 ``items``，从不读 ``CourseSource.uri``，
导致 ``POST /jobs/{id}/run`` 与定时调度都只会写一条 ``fetched=0 / inserted=0``
的空运行记录——「采集任务」形同虚设。

本模块补齐：从源抓取内容 -> 解析成题目 items -> 交回 ``run_job`` 入库。
- ``file`` / ``bank_pack`` / ``bank``：读服务器本地文件（限定 ``COURSE_SOURCE_ROOT``，防穿越）；
- ``url`` / ``web``：SSRF 安全抓取公网 http/https 文本；
- ``bank`` 系列：JSON 数组 -> 中文字段映射成 ``normalize_item`` 认的英文字段；
- 非结构化 HTML/纯文本：返回 ``NEEDS_LLM`` 标记，由编排层（``_resolve_items_from_source``）
  决定是否交给 ``llm_extract`` 抽取（M5-2），**不静默 0 条**。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

log = logging.getLogger(__name__)

# 本地 file/bank_pack 读取只允许落在这个根目录下（防目录穿越）
COURSE_SOURCE_ROOT = os.getenv("COURSE_SOURCE_ROOT", "/opt/course-sources").rstrip("/")

#: 非结构化内容的标记前缀（M5-2）：``parse_items`` 不负责调模型，只如实打标，
#: 由 async 编排层 ``_resolve_items_from_source`` 决定是否转交 ``llm_extract``。
#: 用常量而非散落字符串，避免编排层靠中文子串硬匹配（易漂移）。
NEEDS_LLM = "需 LLM 抽取环节"


def needs_llm(reason: Optional[str]) -> bool:
    """该解析结果是否应交给 LLM 抽取（M5-2 编排判断）。"""
    return bool(reason) and NEEDS_LLM in reason

# 题库包里常见的中文字段 -> normalize_item 认的英文字段
_BANK_CN_TO_EN = {
    "学科": "subject",
    "年级": "grade",
    "章节": "chapter",
    "题型": "type",
    "题干": "stem",
    "选项": "options",
    "答案": "answer",
    "解析": "analysis",
    "难度": "difficulty",
    "卷别": "paper_type",
    "来源省份": "source_province",
    "知识点": "knowledge_points",
}

_SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".txt"})
_SUBTITLE_TIMESTAMP = re.compile(
    r"^(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+"
    r"(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}(?:\s+.*)?$"
)


def validate_video_transcript_uri(uri: str) -> None:
    """The video source currently accepts transcript files, not raw media."""
    path = urlsplit(uri).path if uri.lower().startswith(("http://", "https://")) else uri
    if Path(path).suffix.lower() not in _SUBTITLE_EXTENSIONS:
        raise ValueError("视频源需提供 .srt/.vtt/.txt 字幕或转写文本；原始视频需先经 ASR 转写")


def clean_video_transcript(content: str) -> str:
    """SRT/VTT cue text -> plain text for the existing LLM extractor."""
    lines: List[str] = []
    previous = ""
    raw_lines = (content or "").splitlines()
    for index, raw in enumerate(raw_lines):
        line = raw.strip()
        if not line or line.upper() == "WEBVTT":
            continue
        if line.isdigit():
            following = next(
                (part.strip() for part in raw_lines[index + 1:] if part.strip()),
                "",
            )
            if _SUBTITLE_TIMESTAMP.match(following):
                continue
        if _SUBTITLE_TIMESTAMP.match(line):
            continue
        if line != previous:
            lines.append(line)
        previous = line
    return "\n".join(lines)


def _is_safe_host(host: str) -> bool:
    """阻断 SSRF：localhost / 私网 / 链路本地 / 云元数据。域名放行交解析后复查。"""
    h = (host or "").lower().strip("[]")
    if not h:
        return False
    if h in ("localhost", "0.0.0.0", "::1") or h.endswith(".local"):
        return False
    if h == "169.254.169.254":
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return True  # 域名：交给 _resolve_safe 解析后复查
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast
    )


def _resolve_safe(url: str) -> str:
    """SSRF 闸门：仅 http/https，host 不得为本地/私网/云元数据，且解析出的每个
    IP 也必须公网（防 DNS 重绑定绕过）。返回原 URL。"""
    parts = urlsplit((url or "").strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"采集只支持 http/https，拒绝 {scheme}:// —— {url}")
    host = (parts.hostname or "").lower()
    if not _is_safe_host(host):
        raise ValueError(f"源地址命中本地/私网/云元数据，已拦截：{host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析源主机：{host}（{exc}）")
    for info in infos:
        ip_str = info[4][0]
        try:
            ipo = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ipo.is_private or ipo.is_loopback or ipo.is_link_local
            or ipo.is_reserved or ipo.is_multicast
            or str(ipo) == "169.254.169.254"
        ):
            raise ValueError(f"源地址解析到私网/保留地址，已拦截：{ip_str}")
    return url


#: 常规浏览器 UA。2026-11-12 137 真站点验收实测：w3schools 对
#: httpx 默认 UA（python-httpx/x.y）直接 403，带浏览器 UA 即 200 / 282KB。
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}
_MAX_REDIRECTS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


async def safe_fetch_url(url: str, timeout: float = 20.0) -> str:
    """SSRF 安全抓取公网 URL 文本（需 httpx）。

    两处加固（2026-11-12 真站点验收发现）：
    * 带浏览器 UA，避免公开站点把 python-httpx 默认 UA 当爬虫直接 403；
    * **关闭自动重定向**，改为逐跳手动复查 SSRF。旧版 ``follow_redirects=True``
      只校验初始 URL，站点 302 到 ``169.254.169.254`` 即可绕过闸门打到云元数据，
      是出站防护的真实缺口；现在每一跳都过 ``_resolve_safe``。
    """
    import httpx

    current = url
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, headers=_FETCH_HEADERS
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _resolve_safe(current)
            resp = await client.get(current)
            loc = (
                resp.headers.get("location")
                if resp.status_code in _REDIRECT_CODES else None
            )
            if loc:
                current = urljoin(current, loc)
                continue
            resp.raise_for_status()
            return resp.text
    raise ValueError(f"采集源重定向次数超过上限 {_MAX_REDIRECTS}")


def read_local_file(uri: str) -> str:
    """读取本地文件（``file://`` 或绝对路径），限定在 ``COURSE_SOURCE_ROOT`` 内。"""
    p = (uri or "").strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
    if not p:
        raise ValueError("源文件路径为空")
    path = Path(p).resolve()
    root = Path(COURSE_SOURCE_ROOT).resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"源文件路径不在允许目录 {COURSE_SOURCE_ROOT} 内：{path}")
    if not path.is_file():
        raise ValueError(f"源文件不存在：{path}")
    return path.read_text(encoding="utf-8")


def map_bank_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """题库包一条记录 -> ``normalize_item`` 认的英文字段。兼容中/英键。"""
    out: Dict[str, Any] = {}
    for k in (
        "stem", "answer", "type", "options", "analysis", "difficulty",
        "subject", "grade", "chapter", "paper_type", "source_province",
        "knowledge_points",
    ):
        if k in rec and rec[k] not in (None, ""):
            out[k] = rec[k]
    for cn, en in _BANK_CN_TO_EN.items():
        if cn in rec and rec[cn] not in (None, ""):
            out[en] = rec[cn]
    opts = out.get("options")
    if isinstance(opts, dict):
        out["options"] = [f"{k}. {v}" for k, v in opts.items()]
    kps = out.get("knowledge_points")
    if isinstance(kps, str):
        out["knowledge_points"] = [x for x in re.split(r"[，,、/]", kps) if x.strip()]
    diff = out.get("difficulty")
    if diff is not None:
        try:
            out["difficulty"] = int(diff)
        except (TypeError, ValueError):
            out.pop("difficulty", None)
    return out


def parse_items(content: str, source_type: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """文本 -> 题目 items。

    返回 ``(items, reason)``：``reason`` 非空表示未能解析出结构化题（如需要 LLM
    抽取）。``bank`` / ``bank_pack`` 强制按 JSON 数组解析；其它类型先尝试 JSON，
    失败则明确返回「需 LLM 抽取」而不是静默 0 条。
    """
    st = (source_type or "").lower()
    if st in ("bank", "bank_pack"):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return [], f"题库包不是合法 JSON：{exc}"
        if isinstance(data, dict):
            for key in ("items", "questions", "data", "list"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            return [], "题库包顶层不是题目数组"
        return [map_bank_record(r) for r in data if isinstance(r, dict)], None
    # file / url / web：先尝试 JSON，否则打 NEEDS_LLM 标记交给编排层
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], f"{NEEDS_LLM}：非结构化内容（HTML/纯文本）"
    if isinstance(data, list):
        return [map_bank_record(r) for r in data if isinstance(r, dict)], None
    if isinstance(data, dict):
        for key in ("items", "questions", "data", "list"):
            if isinstance(data.get(key), list):
                items = [map_bank_record(r) for r in data[key] if isinstance(r, dict)]
                return items, None
    return [], f"{NEEDS_LLM}：内容未解析出题目数组"


async def fetch_source_content(source: Any, *, timeout: float = 20.0) -> str:
    """按 ``source.source_type`` 抓取源内容文本。

    ``web`` 源此前被错误地归进 ``read_local_file``（网页 URL 也被当成本地路径读，
    必然失败）。这里改成按 uri 判别：http(s) 走 SSRF 安全抓取，本地路径仍读本地
    文件——既补齐网页抓取能力，又不让历史上指向本地文件的 web 源失效。
    ``url`` 源保持严格 SSRF（非 http(s) 直接由 ``_resolve_safe`` 拒绝）。
    """
    st = (getattr(source, "source_type", None) or "file").lower()
    uri = (getattr(source, "uri", None) or "").strip()
    if st == "video":
        validate_video_transcript_uri(uri)
        if uri.lower().startswith(("http://", "https://")):
            content = await safe_fetch_url(uri, timeout=timeout)
        else:
            content = read_local_file(uri)
        transcript = clean_video_transcript(content)
        if not transcript:
            raise ValueError("视频字幕或转写文本为空")
        return transcript
    if st == "url":
        return await safe_fetch_url(uri, timeout=timeout)
    if st == "web" and uri.lower().startswith(("http://", "https://")):
        return await safe_fetch_url(uri, timeout=timeout)
    if st in ("file", "bank_pack", "bank", "web"):
        return read_local_file(uri)
    raise ValueError(f"不支持的源类型：{st}")
