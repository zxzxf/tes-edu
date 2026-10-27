"""P2 引导式对话 —— 学生意图识别与三档响应路由（T-C1）。

三档定义（与分期书/计划书口径一致）：

- ``direct``  赶时间/直接要答案  -> 简版直给档（llm mode=quick）
- ``explain`` 求讲解/问为什么    -> 讲解优先档（llm mode=explain）
- ``guide``   裸问"这题怎么做"  -> 引导档（llm mode=guide，苏格拉底式，不给最终答案）

设计约束：

1. **规则先行**：显式关键词命中即定档，零 token 开销，覆盖绝大多数真实问句；
2. **LLM 兜底**：规则未覆盖的长问句/混合信号才做一次轻量分类（T-D1 将加请求级缓存）；
3. **失败显式降级为引导档**：分类链路任何一环失败都落到 guide，
   **不静默直给**——直给是唯一可能被学生用来"抄答案"的档位，必须由显式信号触发。

纯函数 ``classify_intent`` 不触网、可单测；``resolve_response_mode`` 是编排侧
入口（async，含兜底链），返回 (intent, mode, source)。
"""
from __future__ import annotations

from app.core import config, llm, tenant_llm

INTENT_DIRECT = "direct"
INTENT_EXPLAIN = "explain"
INTENT_GUIDE = "guide"

# 意图档位 -> llm.solve 的 mode 值（llm.py 的 prompt 按 mode 分支）
TIER_TO_MODE = {
    INTENT_DIRECT: "quick",
    INTENT_EXPLAIN: "explain",
    INTENT_GUIDE: "guide",
}

# 直给档：显式表达"没时间/只要答案"。命中即最高优先级——学生明确放弃了引导。
_DIRECT_KEYWORDS = (
    "直接给答案",
    "直接答案",
    "直接说答案",
    "直接告诉我",
    "直接给结果",
    "不要讲了",
    "别讲了",
    "别提示",
    "不用提示",
    "赶时间",
    "来不及",
    "快点",
    "快一点",
    "着急",
    "just tell me",
    "just give me the answer",
)

# 讲解优先档：显式表达"我想理解为什么/原理"。这类学生要的是解释而非套路。
_EXPLAIN_KEYWORDS = (
    "为什么",
    "原理",
    "讲讲",
    "讲解",
    "解释",
    "解析一下",
    "帮我讲",
    "听不懂",
    "没听懂",
    "怎么来的",
    "怎么想到",
    "本质",
    "意义",
)

# 引导档信号：要答案/要解法但没说"理解原理"。裸问"这题怎么做"落在这里——
# 苏格拉底式的核心立场：要答案的学生同样先进引导档。
_GUIDE_KEYWORDS = (
    "怎么做",
    "怎么解",
    "如何解",
    "求解",
    "答案",
    "解答",
    "这题",
    "这道题",
    "帮我做",
    "教我做",
    "怎么算",
)

# 规则未命中时，多长的问句值得花一次 LLM 分类（短问句直接默认引导档，
# 避免"这题怎么做"这类最常见问句也触发远程调用）。
# T-C5：提到 30，过滤掉纯数学表达式（它们本来就没意图信号，烧 LLM 纯属浪费）。
_LLM_FALLBACK_MIN_LEN = 30


# 短问句里"像求值/计算"的信号：含数字，且带运算符或"多少/等于/的值"。
# 这类题（1+1、求某值）若落进引导档，会被拆成"用手指或画图数 1"式的机械引导，
# 学生体感是"连 1+1 都答不上来"。短计算题一律直给。
_COMPUTE_HINT = ("多少", "等于", "的值", "求值", "计算", "结果", "=?")
_NO_COMPUTE_HINT = ("怎么做", "怎么解", "如何解", "为什么", "讲解", "思路", "原理")


def _looks_like_compute(question: str) -> bool:
    """极短问句是否是"一步可得的计算/求值题"（不触网，纯函数）。"""
    import re

    q = (question or "").strip()
    if len(q) > 20 or not re.search(r"\d", q):
        return False
    if any(k in q for k in _NO_COMPUTE_HINT):
        return False
    if re.search(r"[+\-×÷*^=]|/\d", q):
        return True
    return any(k in q for k in _COMPUTE_HINT)


def classify_intent(question: str, context: dict | None = None) -> str | None:
    """规则先行分类。返回 direct/explain/guide；规则未覆盖返回 None（走兜底链）。

    纯函数：不触网、无副作用，可完整单测。context 预留（题型/难度信号，
    T-C3 题型路由接入后可据此调档）。
    """
    q = (question or "").strip()
    if not q:
        return None
    lowered = q.lower()
    if any(k in q or k in lowered for k in _DIRECT_KEYWORDS):
        return INTENT_DIRECT
    if any(k in q or k in lowered for k in _EXPLAIN_KEYWORDS):
        return INTENT_EXPLAIN
    if any(k in q or k in lowered for k in _GUIDE_KEYWORDS):
        return INTENT_GUIDE
    return None


def _needs_llm_classification(question: str) -> bool:
    """是否值得一次 LLM 分类：规则未覆盖且问句足够长（信息量大）。"""
    q = (question or "").strip()
    return len(q) >= _LLM_FALLBACK_MIN_LEN


async def classify_intent_llm(question: str, context: dict | None = None, *, llm_cfg=None) -> str | None:
    """LLM 轻量兜底分类：一次 temperature=0 的短调用。

    任何失败（无密钥/超时/解析失败/返回非法值）都返回 None，
    由调用方降级到引导档——绝不猜一个直给档出来。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。
    """
    if not config.llm_ready():
        return None
    try:
        cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)
        client = tenant_llm.make_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.chat_model,
            temperature=0,
            max_tokens=8,
            **llm.thinking_kwargs("intent"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是学生提问意图分类器。把学生的问题分三类，只输出类别词本身：\n"
                        "- direct：学生赶时间或明确只要最终答案，放弃引导；\n"
                        "- explain：学生想理解为什么、原理、思路来源；\n"
                        "- guide：学生问这道题怎么做/答案是什么，但没说只要答案。\n"
                        "只输出 direct / explain / guide 之一，不要任何其他文字。"
                    ),
                },
                {"role": "user", "content": question},
            ],
        )
        text = (resp.choices[0].message.content or "").strip().lower()
        if text in (INTENT_DIRECT, INTENT_EXPLAIN, INTENT_GUIDE):
            return text
        # 模型啰嗦时做一次包含匹配兜底（如 "类别：direct"）
        for tier in (INTENT_DIRECT, INTENT_EXPLAIN, INTENT_GUIDE):
            if tier in text:
                return tier
        return None
    except Exception:  # noqa: BLE001 分类失败不阻塞主链路，降级引导档
        return None


async def resolve_response_mode(
    question: str, context: dict | None = None, *, llm_cfg=None
) -> tuple[str, str, str]:
    """编排侧入口：返回 (intent, mode, source)。

    兜底顺序（验收点）：
      1. 规则分类（零成本，覆盖常见问句）；
      2. 规则未命中且问句足够长 -> 一次 LLM 轻量分类（T-C5 起加请求级缓存）；
      3. 以上都失败 -> **默认引导档**（source=default），不静默直给。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。
    """
    # 规则先行：零成本，不走缓存
    intent = classify_intent(question, context)
    source = "rules"
    if intent is not None:
        return intent, TIER_TO_MODE[intent], source

    # 短问句直接默认引导档，不烧 LLM
    if not _needs_llm_classification(question):
        # 短计算/求值题（1+1 等于多少）直给：引导档不给答案，简单题会答得像在敷衍
        if _looks_like_compute(question):
            return INTENT_DIRECT, TIER_TO_MODE[INTENT_DIRECT], "default"
        return INTENT_GUIDE, TIER_TO_MODE[INTENT_GUIDE], "default"

    # T-C5：LLM 分类走缓存（同题不再重烧，TTL 与解题缓存一致）
    from app.core import llm_cache

    store = llm_cache.get_default_store()
    cache_key = f"intent:{llm_cache.normalize_question(question)}"
    cached = await store.get(cache_key)
    if cached and isinstance(cached, dict) and "intent" in cached:
        intent_val = cached["intent"]
        return intent_val, TIER_TO_MODE.get(intent_val, TIER_TO_MODE[INTENT_GUIDE]), "cache"

    # 回源：烧一次 LLM
    intent = await classify_intent_llm(question, context, llm_cfg=llm_cfg)
    source = "llm" if intent else "default"
    if intent is None:
        intent = INTENT_GUIDE
        source = "default"

    # 落缓存（只存成功的结果，失败不落）
    result_tuple = (intent, TIER_TO_MODE[intent], source)
    await store.set(cache_key, {"intent": intent}, ttl=llm_cache.CACHE_TTL_SECONDS)

    return result_tuple
