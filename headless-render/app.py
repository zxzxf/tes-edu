"""独立 headless 渲染服务（供 web-backend 的 discovery 抓取 JS 重度站点正文）。

契约（web-backend 的 ``discovery._render_via_service`` 调用此服务）：
    POST /render   body: {"url": "...", "timeout": 20(可选)}
              -> 200 {"html": "<完整渲染后的 HTML>"}
              -> 400 {"detail": "url 不合法"}
              -> 502 {"detail": "渲染失败：..."}   超时 / 页面加载失败

设计要点：
  * 只监听容器内 8000，不映射宿主机端口（仅 web-backend 经 edu-net 内网可达）。
  * 防御纵深：即便只被 web-backend 调用，仍对目标 URL 做一次私网/元数据地址拦截，
    避免有人误把本服务暴露后沦为 SSRF 跳板。web-backend 侧已先行 SSRF 复查。
  * 每请求启动一个 chromium（无头），用完即关；低频（每晚 discovery 触发时）足够。
  * 容器需以 ``--no-sandbox``（容器内 root 无 namespace）+ ``--disable-dev-shm-usage``
    （小 /dev/shm 下 chromium 易崩）启动。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import urllib.parse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from starlette.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("headless-render")

app = FastAPI(title="Tes Edu Headless Render", version="1.0.0")

RENDER_TIMEOUT = float(os.getenv("RENDER_TIMEOUT", "25"))
# chromium 自身超时略小于服务超时，确保能回 502 而非挂死
_BROWSER_TIMEOUT = max(5.0, RENDER_TIMEOUT - 3.0)

# 仅允许出站到公网；拦截私网/环回/链路本地/云元数据地址（防御纵深，双保险）。
_BLOCK_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),      # 含 169.254.169.254 云元数据
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),             # 唯一本地地址 (ULA)
    ipaddress.ip_network("fe80::/10"),            # 链路本地
)


def _host_is_blocked(host: str) -> bool:
    """目标主机解析后任一 IP 落在私网/元数据段则返回 True（拦截）。"""
    if host.lower() in ("localhost", "0.0.0.0", "::1", "ip6-localhost"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # 解析不了就不渲染（宁可失败，不猜测）
        return True
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return True
        if any(addr in net for net in _BLOCK_RANGES):
            return True
    return False


def _ssrf_ok(url: str) -> bool:
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.hostname:
        return False
    return not _host_is_blocked(parts.hostname)


class RenderRequest(BaseModel):
    url: HttpUrl
    timeout: float | None = None


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/render")
async def render(body: RenderRequest):
    url = str(body.url)
    if not _ssrf_ok(url):
        raise HTTPException(status_code=400, detail="目标地址被拦截（私网/元数据/非法）")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise HTTPException(status_code=503, detail="渲染运行时未安装 playwright")

    to = float(body.timeout or RENDER_TIMEOUT)
    browser = None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                ]
            )
            page = await browser.new_page()
            try:
                await page.goto(
                    url, wait_until="networkidle", timeout=_BROWSER_TIMEOUT * 1000
                )
            except Exception:  # noqa: BLE001 networkidle 超时则退到 load 再取正文
                await page.goto(url, wait_until="load", timeout=_BROWSER_TIMEOUT * 1000)
            html = await page.content()
        return JSONResponse({"html": html})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 渲染失败 -> 502，由调用方按拒绝处理
        log.warning("render failed %s: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"渲染失败：{exc}")
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, workers=1)
