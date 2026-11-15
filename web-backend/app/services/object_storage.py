"""③·对象存储（MinIO，ROADMAP #28 配套）。

把讲解视频 mp4 存到 MinIO，替代「本地静态目录」。外部契约不变：
- 学生对内仍拿 ``/api/student/explanation-videos/{id}.mp4`` 这样的相对 URL；
- web-backend 内部用本模块的 ``fetch_video`` 从 MinIO 取流再吐给客户端
  （MinIO 仅内网可达，无需对外暴露端口 / 改网关）。

未配置 ``minio_endpoint`` 时本模块全部返回 ``None``，调用方回退到本地文件，
保证未部署 MinIO 的环境（本地 / 单测）行为不变。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

from app.core.config import settings

logger = logging.getLogger("object_storage")

_client = None
_client_ready = False  # 避免每次都重试 import / 构造


def _get_client():
    """惰性构造 MinIO 客户端；未配置或 SDK 缺失返回 None（调用方降级）。"""
    global _client, _client_ready
    if _client_ready:
        return _client
    _client_ready = True
    if not settings.minio_endpoint:
        return None
    try:
        from minio import Minio
    except ImportError:  # 未装 SDK：降级本地，不崩
        logger.warning("minio SDK 未安装，对象存储不可用，回退本地静态")
        return None
    try:
        _client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key or None,
            secret_key=settings.minio_secret_key or None,
            secure=settings.minio_use_ssl,
        )
        # 桶不存在则建（生产由部署脚本预建亦可）
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
        logger.info("MinIO 客户端就绪 bucket=%s", settings.minio_bucket)
    except Exception as e:  # noqa: BLE001
        logger.warning("MinIO 初始化失败，回退本地静态：%s", e)
        _client = None
    return _client


def is_enabled() -> bool:
    return _get_client() is not None


async def upload_video(local_path: str, object_name: str) -> bool:
    """上传 mp4 到 MinIO；成功返回 True，否则 False（调用方保留本地文件兜底）。"""
    client = _get_client()
    if client is None or not hasattr(client, "fput_object"):
        return False
    try:
        await asyncio.to_thread(
            client.fput_object,
            settings.minio_bucket,
            object_name,
            local_path,
            content_type="video/mp4",
        )
        logger.info("uploaded %s -> %s/%s", local_path, settings.minio_bucket, object_name)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("MinIO 上传失败，保留本地：%s", e)
        return False


async def fetch_video(object_name: str) -> Tuple[Optional[bytes], Optional[str]]:
    """从 MinIO 取回对象字节；不存在 / 未配置返回 (None, None)。"""
    client = _get_client()
    if client is None or not hasattr(client, "get_object"):
        return None, None
    try:
        resp = await asyncio.to_thread(
            client.get_object, settings.minio_bucket, object_name
        )
        try:
            data = await asyncio.to_thread(resp.read)
            return data, "video/mp4"
        finally:
            resp.close()
            resp.release_conn()
    except Exception as e:  # noqa: BLE001
        # NoSuchKey 等：交给调用方走本地回退
        logger.debug("MinIO 取 %s 失败：%s", object_name, e)
        return None, None
