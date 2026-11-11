"""M5 采集源抓取与解析（让「采集任务」真正能采）。

此前 ``run_job`` 只接收调用方显式传入的 ``items``，从不读 ``CourseSource.uri``，
导致 ``POST /jobs/{id}/run`` 与定时调度都只会写一条 ``fetched=0 / inserted=0``
的空运行记录——「采集任务」形同虚设。

本模块补齐：从源抓取内容 -> 解析成题目 items -> 交回 ``run_job`` 入库。
- ``file`` / ``bank_pack``：读服务器本地文件（限定 ``COURSE_SOURCE_ROOT``，防穿越）；
- ``url`` / ``web``：SSRF 安全抓取公网 http/https 文本；
- ``bank`` 系列：JSON 数组 -> 中文字段映射成 ``normalize_item`` 认的英文字段；
- 非结构化 HTML/纯文本：如实返回「需 LLM 抽取（尚未接入）」，不再静默 0。
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
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# 本地 file/bank_pack 读取只允许落在这个根目录下（防目录穿越）
COURSE_SOURCE_ROOT = os.getenv("COURSE_SOURCE_ROOT", "/opt/course-sources").rstrip("/")

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


async def safe_fetch_url(url: str, timeout: float = 20.0) -> str:
    """SSRF 安全抓取公网 URL 文本（需 httpx）。"""
    _resolve_safe(url)
    import httpx

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


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
    # file / url / web：先尝试 JSON，否则报需 LLM 抽取
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], "非结构化内容（HTML/纯文本），需 LLM 抽取环节（尚未接入）"
    if isinstance(data, list):
        return [map_bank_record(r) for r in data if isinstance(r, dict)], None
    if isinstance(data, dict):
        for key in ("items", "questions", "data", "list"):
            if isinstance(data.get(key), list):
                items = [map_bank_record(r) for r in data[key] if isinstance(r, dict)]
                return items, None
    return [], "内容未解析出题目数组，需 LLM 抽取环节（尚未接入）"


async def fetch_source_content(source: Any, *, timeout: float = 20.0) -> str:
    """按 ``source.source_type`` 抓取源内容文本。"""
    st = (getattr(source, "source_type", None) or "file").lower()
    uri = getattr(source, "uri", None) or ""
    if st == "url":
        return await safe_fetch_url(uri, timeout=timeout)
    if st in ("file", "bank_pack", "bank", "web"):
        return read_local_file(uri)
    raise ValueError(f"不支持的源类型：{st}（video 抽取未接入）")
