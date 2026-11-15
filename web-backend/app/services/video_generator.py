"""④·数字人讲题短视频生成管线（ROADMAP #28）。

把重型合成藏在可替换接口 ``VideoGenerator`` 之后：

- ``NoopGenerator``：管线未启用时的占位，直接标记不可用。
- ``LiteVideoGenerator``：无 GPU 的轻量实现——ffmpeg lavfi 渲染字幕幻灯片 +
  可选阿里云百炼 CosyVoice 配音；缺 ffmpeg / 中文字体 / TTS 密钥时**优雅降级**
  并写明原因（状态置 failed，不崩溃）。
- （未来）``DigitalHumanGenerator``：接 GPU 服务器上的 CosyVoice + SadTalker /
  Wav2Lip 肖像口播；接口不变，只在配置里换实现即可。

视频落 ``settings.explanation_video_dir``，由 main.py 的静态路由对外提供。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import shutil
import subprocess
from typing import Optional, Tuple

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.explanation_video import ExplanationVideo

logger = logging.getLogger("video_generator")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


# 常见中文字体路径（ffmpeg drawtext 需要字体文件才能渲染 CJK）。
# Dockerfile 已装 fonts-wqy-zenhei，命中第一条即可。
_CJK_FONTS = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/chinese/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


def _find_cjk_font() -> Optional[str]:
    for p in _CJK_FONTS:
        if os.path.exists(p):
            return p
    return None


def _which(bin_name: str) -> bool:
    if os.path.exists(bin_name):
        return True
    return shutil.which(bin_name) is not None


def _ffprobe_bin() -> Optional[str]:
    """ffprobe 通常与 ffmpeg 同目录；优先用替换得到的路径，否则回退 PATH 查找。"""
    cand = settings.ffmpeg_bin.replace("ffmpeg", "ffprobe")
    if _which(cand):
        return cand
    return shutil.which("ffprobe")


class VideoGenerator:
    """数字人讲题视频生成器（可替换实现）。

    返回 ``(ok, duration_sec, error)``。
    """

    async def generate(self, script: str, out_path: str) -> Tuple[bool, Optional[float], Optional[str]]:
        raise NotImplementedError


class NoopGenerator(VideoGenerator):
    async def generate(self, script, out_path):
        return False, None, "数字人管线未启用（digital_human_enabled=false）"


class LiteVideoGenerator(VideoGenerator):
    """无 GPU 轻量实现：ffmpeg 字幕幻灯片 + 可选 TTS 配音。"""

    async def generate(self, script: str, out_path: str) -> Tuple[bool, Optional[float], Optional[str]]:
        ffmpeg = settings.ffmpeg_bin
        if not _which(ffmpeg):
            return False, None, f"ffmpeg 不可用（{ffmpeg}），无法合成视频"
        font = _find_cjk_font()
        if not font:
            return False, None, "未找到中文字体，无法渲染字幕（安装 wqy/Noto CJK 后重试）"

        # 1) 可选：百炼 TTS 配音
        audio_path: Optional[str] = None
        if settings.dashscope_tts_api_key:
            audio_path = await self._tts(script, out_path)

        # 2) ffmpeg 合成
        ok, err = await self._render(script, out_path, audio_path, font)
        if not ok:
            return False, None, err
        # 3) 用 ffprobe 取真实时长回填 duration_sec（取不到也不报错，字段可空）
        duration = await self._probe_duration(out_path)
        return True, duration, None

    async def _tts(self, script: str, out_path: str) -> Optional[str]:
        """百炼 CosyVoice TTS：best-effort，失败则降级为无声视频。"""
        try:
            import httpx

            audio_path = out_path + ".mp3"
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{settings.dashscope_tts_base_url}/{settings.dashscope_tts_model}?action=text-generation",
                    headers={
                        "Authorization": f"Bearer {settings.dashscope_tts_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"input": {"text": script}, "parameters": {"format": "mp3"}},
                )
                if resp.status_code != 200:
                    logger.warning("TTS 失败 %s: %s", resp.status_code, resp.text[:200])
                    return None
                data = resp.json()
                audio_b64 = data.get("output", {}).get("audio")
                if not audio_b64:
                    return None
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audio_b64))
            return audio_path
        except Exception:  # noqa: BLE001
            logger.exception("TTS 异常，降级无声视频")
            return None

    async def _render(
        self, script: str, out_path: str, audio_path: Optional[str], font: str
    ) -> Tuple[bool, Optional[str]]:
        """ffmpeg lavfi 逐行字幕幻灯片；有配音则混音，按最短流截断。"""
        lines = [ln.strip() for ln in script.splitlines() if ln.strip()] or [script]
        textfile = out_path + ".txt"
        with open(textfile, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        n = max(1, len(lines))
        total = n * 4  # 每行约 4s
        filter_chain = (
            f"color=c=white:s=1280x720:d={total}:r=25[bg];"
            f"[bg]drawtext=textfile='{textfile}':fontfile='{font}':"
            f"fontcolor=black:fontsize=40:line_spacing=20:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:fix_bounds=true"
        )
        if audio_path and os.path.exists(audio_path):
            cmd = [
                settings.ffmpeg_bin, "-f", "lavfi", "-i", filter_chain,
                "-i", audio_path, "-t", str(total), "-pix_fmt", "yuv420p",
                "-shortest", out_path,
            ]
        else:
            cmd = [
                settings.ffmpeg_bin, "-f", "lavfi", "-i", filter_chain,
                "-t", str(total), "-pix_fmt", "yuv420p", out_path,
            ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return False, f"ffmpeg 失败({proc.returncode}): {stderr.decode(errors='ignore')[:300]}"
            return True, None
        except Exception as e:  # noqa: BLE001
            return False, f"ffmpeg 执行异常：{e}"
        finally:
            try:
                os.remove(textfile)
            except OSError:
                pass

    async def _probe_duration(self, out_path: str) -> Optional[float]:
        """用 ffprobe 取真实视频时长（秒），取不到返回 None，不影响生成结果。"""
        fb = _ffprobe_bin()
        if not fb or not os.path.exists(out_path):
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                fb, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                out_path,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            sout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            val = sout.decode(errors="ignore").strip()
            return float(val) if val else None
        except Exception:  # noqa: BLE001
            logger.exception("ffprobe 取时长失败")
            return None


def get_generator() -> VideoGenerator:
    if settings.digital_human_enabled:
        return LiteVideoGenerator()
    return NoopGenerator()


async def run_generation(row_id: str) -> None:
    """由 BackgroundTasks（或 Kafka worker）调用：加载行 → 生成 → 回写状态。"""
    async with SessionLocal() as db:
        row = await db.get(ExplanationVideo, row_id)
        if row is None:
            logger.warning("video row %s 不存在", row_id)
            return
        if row.status == "done":
            return
        row.status = "processing"
        row.error = None
        await db.commit()

        gen = get_generator()
        os.makedirs(settings.explanation_video_dir, exist_ok=True)
        out_path = os.path.join(settings.explanation_video_dir, f"{row.id}.mp4")
        try:
            ok, duration, err = await gen.generate(row.script or row.question_text, out_path)
        except Exception as e:  # noqa: BLE001
            ok, duration, err = False, None, f"生成异常：{e}"

        if ok and os.path.exists(out_path):
            # ③·对象存储：best-effort 上传 MinIO；失败不影响本地兜底（流式路由会回退本地）。
            object_name = f"explanation_videos/{row.id}.mp4"
            try:
                from app.services import object_storage

                await object_storage.upload_video(out_path, object_name)
            except Exception:  # noqa: BLE001
                logger.exception("MinIO 上传失败（保留本地兜底）")
            # video_url 保持相对路径：由 serve 路由统一从 MinIO / 本地取流。
            row.status = "done"
            row.video_url = f"/api/student/explanation-videos/{row.id}.mp4"
            row.duration_sec = duration
            row.error = None
        else:
            row.status = "failed"
            row.error = err or "未知失败"
        await db.commit()
        logger.info("video gen %s -> %s", row.id, row.status)
