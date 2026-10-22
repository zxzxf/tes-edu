"""OCR / 多模态识别：拍照识题抽题面。

真实模式：配置视觉模型（VLM_BASE_URL/VLM_MODEL + LLM_API_KEY）后，图片转 base64 交给视觉模型识别。
dev 模式：返回明确标注的降级文本，不阻塞文本题主链路。
"""
from __future__ import annotations

import base64

from app.core import config


async def recognize(image_bytes: bytes) -> str:
    if config.vlm_ready():
        return await _vlm_recognize(image_bytes)
    return (
        "[dev-未配置视觉模型] 拍照识题未启用，请先用文本输入；"
        "配置 VLM_BASE_URL / VLM_MODEL / LLM_API_KEY 后启用。"
    )


async def _vlm_recognize(image_bytes: bytes) -> str:
    from openai import AsyncOpenAI  # 延迟导入

    client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.VLM_BASE_URL)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    resp = await client.chat.completions.create(
        model=config.VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "请识别题面文字与公式，仅输出题干原文，不要解答。"},
                ],
            }
        ],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()
