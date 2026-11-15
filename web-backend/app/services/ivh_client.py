"""腾讯云智能数智人（IVH）aPaaS 播报视频制作客户端（ROADMAP #28 · 真数字人）。

只做「提交任务 -> 轮询进度 -> 下载成品」三件事，不碰 DB / MinIO / 落库，
由 ``video_generator.DigitalHumanGenerator`` 编排。

官方契约要点（cloud.tencent.com/document/product/1240/118475 等）：
- 域名 ``https://gw.tvs.qq.com``；签名是**每次请求动态计算**的，不是平台给的静态 sign。
- 公共参数以 QueryString 携带：``appkey`` / ``timestamp``（秒，误差 <=5 分钟）/ ``signature``。
- 签名原文 = 除 signature 外的 query 参数按键名**字典序**拼成 ``k=v&k=v``；
  再以 ``accesstoken`` 为密钥做 HmacSHA256 -> base64 -> urlencode。
  **请求体不参与签名**。
- 请求体必须是 ``{"Header": {}, "Payload": {...}}``（Header 是请求体字段，不是 HTTP 头）。
- 照片免训练接口 ``phototovideonotrain``：一张公网形象照 + 文本/音频 -> 肖像口播视频。
- ``getprogress`` 轮询，Status=SUCCESS 时取 ``MediaUrl``；成品只保留 **7 天**，必须立即拉回。
- 输出 720P 时每秒视频约需 20 秒渲染，因此必须异步轮询，不可短超时同步等待。

安全：``accesstoken`` 只从 ``settings``（环境变量）读取，绝不写日志、绝不落文件。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import logging
import os
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import httpx

from app.core.config import settings

logger = logging.getLogger("ivh_client")

PHOTO_SUBMIT_PATH = "/v2/ivh/videomaker/broadcastservice/phototovideonotrain"
PROGRESS_PATH = "/v2/ivh/videomaker/broadcastservice/getprogress"


class IVHError(Exception):
    """IVH 调用失败；``retryable=False`` 表示参数/权限类错误，不该重试。"""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def build_signature(appkey: str, accesstoken: str, timestamp: int) -> str:
    """按官方算法生成 urlencode 后的 signature（纯函数，便于单测）。"""
    signing_content = f"appkey={appkey}&timestamp={timestamp}"
    digest = hmac.new(
        accesstoken.encode("utf-8"),
        signing_content.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return urllib.parse.quote(base64.b64encode(digest).decode("ascii"), safe="")


def signed_url(path: str, appkey: str, accesstoken: str, base_url: str, timestamp: int) -> str:
    """拼接带公共参数与动态签名的完整 URL。"""
    sig = build_signature(appkey, accesstoken, timestamp)
    return f"{base_url.rstrip('/')}{path}?appkey={urllib.parse.quote(appkey, safe='')}&timestamp={timestamp}&signature={sig}"


def normalize_ssml(text: str, max_chars: int) -> str:
    """把讲题脚本清洗成接口可接受的 InputSsml。

    约束：不能换行、上限 300 字、符号需转义、不少于 4 字。
    多行脚本合并为一行（用中文分号分隔，保留语义），超出截断并加省略号。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    joined = "；".join(lines) if lines else (text or "").strip()
    joined = joined.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    # html.escape 会处理 & < > " '，避免题干里的符号被当成 SSML 标签解析
    escaped = html.escape(joined, quote=True)
    if len(escaped) > max_chars:
        cut = escaped[: max(0, max_chars - 3)]
        # 转义会膨胀字符数，截断可能把 &amp; / &quot; / &#x27; 拦腰切断，
        # 产生非法 SSML；把末尾不完整的实体整段去掉。
        amp = cut.rfind("&")
        if amp != -1 and ";" not in cut[amp:]:
            cut = cut[:amp]
        escaped = cut.rstrip() + "..."
    return escaped


def _check_header(data: Dict[str, Any], stage: str) -> Dict[str, Any]:
    """校验响应 Header.Code；非 0 抛 IVHError。返回 Payload。"""
    if not isinstance(data, dict):
        raise IVHError(f"{stage}: 响应不是 JSON 对象")
    header = data.get("Header") or {}
    code = header.get("Code", 0)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = -1
    if code != 0:
        msg = str(header.get("Message") or "未知错误")
        raise IVHError(f"{stage}: IVH Code={code} {msg[:200]}", retryable=False)
    payload = data.get("Payload")
    if not isinstance(payload, dict):
        raise IVHError(f"{stage}: 响应缺少 Payload")
    return payload


async def _post(path: str, payload: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    """发起一次带动态签名的 aPaaS POST。"""
    appkey = settings.ivh_appkey
    token = settings.ivh_accesstoken
    if not appkey or not token:
        raise IVHError("IVH 未配置 appkey/accesstoken", retryable=False)
    url = signed_url(path, appkey, token, settings.ivh_base_url, int(time.time()))
    body = {"Header": {}, "Payload": payload}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json;charset=utf-8"},
            )
    except httpx.HTTPError as e:
        raise IVHError(f"IVH 网络异常: {type(e).__name__}: {e}", retryable=True) from e
    if resp.status_code != 200:
        raise IVHError(f"IVH HTTP {resp.status_code}: {resp.text[:200]}", retryable=True)
    try:
        return resp.json()
    except ValueError as e:
        raise IVHError(f"IVH 响应非 JSON: {resp.text[:200]}", retryable=True) from e


async def submit_photo_video(
    script: str,
    *,
    ref_photo_url: str,
    driver_type: str = "Text",
    audio_url: Optional[str] = None,
) -> str:
    """提交照片免训练视频制作任务，返回 TaskId。"""
    if not ref_photo_url:
        raise IVHError("IVH 未配置 ivh_ref_photo_url（形象照需公网可访问）", retryable=False)

    payload: Dict[str, Any] = {"RefPhotoUrl": ref_photo_url}

    if driver_type == "OriginalVoice" and audio_url:
        # 音频驱动：不传 InputSsml / SpeechParam，故不做文本字数校验
        payload["DriverType"] = "OriginalVoice"
        payload["InputAudioUrl"] = audio_url
    else:
        # Text 驱动：平台内置 TTS，SpeechParam 为必填，InputSsml 至少 4 字
        ssml = normalize_ssml(script, settings.ivh_max_ssml_chars)
        if len(ssml) < 4:
            raise IVHError("讲解文本过短（InputSsml 至少 4 字）", retryable=False)
        payload["DriverType"] = "Text"
        payload["InputSsml"] = ssml
        payload["SpeechParam"] = {
            "TimbreKey": settings.ivh_timbre_key,
            "Speed": settings.ivh_speed,
            "Volume": settings.ivh_volume,
        }

    data = await _post(PHOTO_SUBMIT_PATH, payload, timeout=settings.ivh_request_timeout)
    out = _check_header(data, "提交视频制作")
    task_id = out.get("TaskId")
    if not task_id:
        raise IVHError("提交成功但未返回 TaskId", retryable=False)
    logger.info("IVH 任务已提交 task_id=%s driver=%s", task_id, payload["DriverType"])
    return str(task_id)


async def query_progress(task_id: str) -> Tuple[str, Optional[str], Optional[str], int]:
    """查询一次进度。

    返回 ``(status, media_url, subtitles_url, duration_ms)``；
    status ∈ COMMIT / MAKING / SUCCESS / FAIL。
    """
    data = await _post(
        PROGRESS_PATH, {"TaskId": task_id}, timeout=settings.ivh_request_timeout
    )
    out = _check_header(data, "查询进度")
    status = str(out.get("Status") or "").upper()
    media = out.get("MediaUrl") or None
    subtitles = out.get("SubtitlesUrl") or None
    try:
        duration_ms = int(out.get("Duration") or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return status, media, subtitles, duration_ms


async def wait_for_media(task_id: str) -> Tuple[str, Optional[str], int]:
    """轮询直到出片；失败/超时抛 IVHError。返回 (MediaUrl, SubtitlesUrl, DurationMs)。"""
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, settings.ivh_poll_timeout)
    interval = max(1.0, settings.ivh_poll_interval)
    last_status = "COMMIT"
    while True:
        status, media, subtitles, duration_ms = await query_progress(task_id)
        last_status = status or last_status
        if status == "SUCCESS":
            if not media:
                raise IVHError("任务成功但未返回 MediaUrl", retryable=False)
            return media, subtitles, duration_ms
        if status == "FAIL":
            raise IVHError(f"IVH 制作失败（task={task_id}）", retryable=False)
        if loop.time() + interval >= deadline:
            raise IVHError(
                f"IVH 出片超时（{settings.ivh_poll_timeout:.0f}s，最后状态 {last_status}）",
                retryable=True,
            )
        await asyncio.sleep(interval)


async def download_video(media_url: str, out_path: str) -> None:
    """把成品拉回本地（成品仅保留 7 天，绝不把 MediaUrl 直接落库）。"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp_path = out_path + ".ivh.part"
    try:
        async with httpx.AsyncClient(timeout=settings.ivh_download_timeout) as client:
            async with client.stream("GET", media_url) as resp:
                if resp.status_code != 200:
                    raise IVHError(
                        f"下载成品失败 HTTP {resp.status_code}", retryable=True
                    )
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
        if os.path.getsize(tmp_path) <= 0:
            raise IVHError("下载成品为空文件", retryable=True)
        os.replace(tmp_path, out_path)
    except httpx.HTTPError as e:
        raise IVHError(f"下载成品网络异常: {type(e).__name__}: {e}", retryable=True) from e
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    logger.info("IVH 成品已归档 %s (%d bytes)", out_path, os.path.getsize(out_path))
