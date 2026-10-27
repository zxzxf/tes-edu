"""预设供应商清单服务层（W6 T-M3）：读清单 + 幂等种子。

清单存库热更新；seed_presets 仅插入缺失行（provider 已存在则跳过），
线上调整厂商可直接改库或后续加管理接口，不必发版。
"""

from sqlalchemy import select

from app.models.byom_preset import VendorPreset

# 计划书 §4 的 8 家 + 自定义（自定义无预填 base_url）。
# 字段：provider, display_name, base_url, models, embed_base_url, embed_model, notes, sort_order
PRESETS = [
    ("aliyun", "阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "qwen-plus,qwen-max,qwen-turbo,qwen-long,qwen3-235b-a22b,qwen2.5-72b-instruct",
     "", "text-embedding-v3", "embedding 同域 /embeddings", 1),
    ("tencent", "腾讯云混元", "https://api.hunyuan.cloud.tencent.com/v1",
     "hunyuan-pro,hunyuan-turbo,hunyuan-standard,hunyuan-lite",
     "", "", "须 OpenAI 兼容协议", 2),
    ("ark", "火山方舟", "https://ark.cn-beijing.volces.com/api/v3",
     "doubao-pro-32k,doubao-lite-32k,deepseek-r1-250120,deepseek-v3-250324",
     "", "", "endpoint 含推理接入点 ID", 3),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
     "deepseek-chat,deepseek-reasoner", "", "", "", 4),
    ("openai", "OpenAI", "https://api.openai.com/v1",
     "gpt-4o,gpt-4o-mini,gpt-4-turbo,gpt-3.5-turbo", "", "", "", 5),
    ("azure", "Azure OpenAI", "https://<resource>.openai.azure.com",
     "<deployment-name>", "", "",
     "路径非标准 /openai/deployments/<dep>/chat/completions?api-version=...", 6),
    ("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
     "glm-4-plus,glm-4-flash,glm-4-air,glm-4-long", "", "", "", 7),
    ("moonshot", "Moonshot", "https://api.moonshot.cn/v1",
     "moonshot-v1-8k,moonshot-v1-32k,moonshot-v1-128k", "", "", "", 8),
    ("custom", "自定义（Custom）", "",
     "", "", "", "任意 OpenAI 兼容端点，base_url 自行填写", 99),
]


async def list_presets(db) -> list[dict]:
    rows = (
        await db.execute(
            select(VendorPreset)
            .where(VendorPreset.enabled.is_(True))
            .order_by(VendorPreset.sort_order)
        )
    ).scalars().all()
    return [
        {
            "provider": r.provider,
            "display_name": r.display_name,
            "base_url": r.base_url,
            "models": [m for m in (r.models or "").split(",") if m],
            "embed_base_url": r.embed_base_url or "",
            "embed_model": r.embed_model or "",
            "notes": r.notes or "",
        }
        for r in rows
    ]


async def seed_presets(db) -> int:
    """幂等种子：仅插入缺失的 provider 行。返回新增数。"""
    existing = set((await db.execute(select(VendorPreset.provider))).scalars().all())
    added = 0
    for p in PRESETS:
        provider, name, base, models, emb, emb_model, notes, sort = p
        if provider in existing:
            continue
        db.add(
            VendorPreset(
                provider=provider,
                display_name=name,
                base_url=base,
                models=models,
                embed_base_url=emb or None,
                embed_model=emb_model or None,
                notes=notes,
                sort_order=sort,
            )
        )
        added += 1
    if added:
        await db.commit()
    return added
