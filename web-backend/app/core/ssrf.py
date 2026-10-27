"""SSRF 闸门（W6 BYOM T-M3，web-backend 侧）。

与 agent-backend/app/core/tenant_llm.py::assert_safe_base_url 保持一致口径：
- 仅 https；
- host 不得为 localhost / RFC1918 私网（10/8、172.16/12、192.168/16）/ 云元数据
  169.254.169.254；
- 可选域名白名单 ``BYOM_ALLOWED_DOMAINS``（逗号分隔后缀，如 ``*.aliyuncs.com``）。

管理端保存租户配置、连通性测试前，后端兜底校验——前端校验只是体验层，
真正的安全边界在后端（计划书 §6/§7）。
"""

import ipaddress
import os
from urllib.parse import urlsplit


class SSRFRejected(Exception):
    """base_url 触发 SSRF 闸门。"""


def _allowed_suffixes() -> list[str]:
    raw = os.getenv("BYOM_ALLOWED_DOMAINS", "")
    out: list[str] = []
    for s in raw.split(","):
        s = s.strip().lower().lstrip("*.").strip(".")
        if s:
            out.append(s)
    return out


def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def assert_safe_base_url(url) -> str:
    """SSRF 闸门：返回规范化 https URL；不合规抛 :class:`SSRFRejected`。"""
    if not url or not isinstance(url, str):
        raise SSRFRejected("base_url 为空或非法")
    try:
        parts = urlsplit(url.strip())
    except Exception as e:  # noqa: BLE001
        raise SSRFRejected(f"无法解析 base_url: {url!r}") from e
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        raise SSRFRejected(f"base_url 仅允许 https，拒绝 {scheme}:// —— {url}")
    host = (parts.hostname or "").lower()
    if not host:
        raise SSRFRejected(f"base_url 缺少 host: {url}")
    if host in ("localhost", "0.0.0.0", "::1", "[::]") or host.endswith(".local"):
        raise SSRFRejected(f"base_url 命中本地主机: {host}")
    if _is_private_ip(host) or host == "169.254.169.254":
        raise SSRFRejected(f"base_url 命中私网/云元数据地址: {host}")
    suffixes = _allowed_suffixes()
    if suffixes and not any(host == s or host.endswith("." + s) for s in suffixes):
        raise SSRFRejected(f"base_url 不在允许域名白名单: {host}")
    return url
