"""OCR / 多模态识别：拍照识题抽题面。

真实模式：把图片转 base64 交给视觉模型（VLM）识别。VLM 配置由调用方按租户解析后传入
（base_url / api_key / model），不再依赖全局 env——这样拍照识题可直接复用学校（租户）
在 BYOM 后台配置的视觉模型，缺省则回落平台全局 VLM（见 tenant_llm.resolve_llm_config）。
"""
from __future__ import annotations

import base64


async def recognize(
    image_bytes: bytes,
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    """调用视觉模型识别题面文字。

    参数
    ----
    base_url / api_key / model: 已解析好的 VLM 端点（来自租户 BYOM 配置，回落全局）。
    调用方（orchestrator.handle_image）须先确认三者齐备再调用本函数。
    """
    return await _vlm_recognize(image_bytes, base_url=base_url, api_key=api_key, model=model)


async def _vlm_recognize(image_bytes: bytes, *, base_url: str, api_key: str, model: str) -> str:
    from openai import AsyncOpenAI  # 延迟导入

    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    resp = await client.chat.completions.create(
        model=model,
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
