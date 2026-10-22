"""LLM 解答引擎：真实模式调 OpenAI 兼容 /v1/chat/completions（DeepSeek / 通义 / Qwen）。

dev 模式（无 LLM_BASE_URL/KEY）返回"基于真实检索上下文拼装"的讲解而非占位字符串，
保证链路可跑通；生产补上密钥即自动切为真实大模型。
"""
from __future__ import annotations

import json

from app.core import config

_SYSTEM = (
    "你是一名高中数学导师，采用苏格拉底式引导讲解。\n"
    "规则：\n"
    "1) 必须依据【检索到的知识点与例题】作答，不得编造；检索内容不足时先说明并给出通法。\n"
    "2) 默认讲解优先：先点明考点，再分步骤推导，关键分叉点给提示而不是直接给最终答案。\n"
    "3) mode=quick 给简版直给；mode=similar 生成 3 道变式题。\n"
    "4) 输出严格 JSON 且不要包裹 markdown：mode=similar 时为 {\"similar\": [\"...\",\"...\",\"...\"]}；\n"
    "   其余模式为 {\"answer\": \"...\", \"steps\": [\"...\"]}。"
)


def _build_messages(question: str, context: dict | None, mode: str) -> list[dict]:
    chunks = (context or {}).get("chunks", [])
    if chunks:
        ctx_text = "\n\n".join(
            f"[例题{i + 1}] {c.get('text', '')}\n知识点：{c.get('knowledge_point', '')}"
            for i, c in enumerate(chunks)
        )
    else:
        ctx_text = "（无检索结果，请说明并给出通用思路）"
    user = f"【检索到的知识点与例题】\n{ctx_text}\n\n【学生问题】\n{question}\n\n【模式】{mode}"
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").replace("json", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


async def solve(question: str, context: dict | None = None, mode: str = "explain") -> dict:
    if config.llm_ready():
        return await _remote_solve(question, context, mode)
    return _dev_solve(question, context, mode)


async def _remote_solve(question: str, context: dict | None, mode: str) -> dict:
    from openai import AsyncOpenAI  # 延迟导入

    client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    resp = await client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=_build_messages(question, context, mode),
        temperature=0.3,
    )
    content = resp.choices[0].message.content or ""
    data = _parse_json(content)
    answer = data.get("answer") or content
    steps = data.get("steps") or []
    # 不同模型返回形态不一：answer 可能是数字，steps 可能是单个字符串
    if isinstance(steps, str):
        steps = [steps]
    return {"answer": str(answer), "steps": [str(s) for s in steps]}


def _dev_solve(question: str, context: dict | None, mode: str) -> dict:
    """dev 回退：用检索到的真实上下文拼装讲解（非占位字符串）。"""
    chunks = (context or {}).get("chunks", [])
    points = list(dict.fromkeys(c.get("knowledge_point") for c in chunks if c.get("knowledge_point")))
    point_text = "、".join(points) if points else "待标注（检索未命中，需补题）"

    if mode == "quick":
        first = chunks[0]["text"][:60] if chunks else question
        return {
            "answer": f"【简版直给】考点：{point_text}。通法要点：{first}",
            "steps": ["直接给出关键步骤", "套用通法求解"],
        }

    evidence = "\n".join(
        f"- {c['text'][:60]}（知识点：{c.get('knowledge_point', '')}，相关度 {c.get('score', 0)}）"
        for c in chunks[:3]
    ) or "- 无命中例题"
    steps = [
        "审题：明确已知条件与所求量",
        f"知识点定位：{point_text}",
        "分步推导：结合检索到的同类例题通法逐步化简",
        "总结易错点与通法",
    ]
    answer = (
        f"[dev-无密钥回退] 已检索到 {len(chunks)} 条相关例题：\n{evidence}\n"
        f"本题建议从「{point_text}」入手，先明确条件再套通法。"
        f"配置 LLM_BASE_URL / LLM_API_KEY 后由大模型生成完整讲解。"
    )
    return {"answer": answer, "steps": steps}


async def suggest_similar(question: str, context: dict | None = None) -> list[str]:
    if config.llm_ready():
        from openai import AsyncOpenAI  # 延迟导入

        client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=_build_messages(question, context, "similar"),
            temperature=0.7,
        )
        content = resp.choices[0].message.content or ""
        data = _parse_json(content)
        sims = data.get("similar")
        if isinstance(sims, str):
            sims = [sims]
        if sims:
            return [str(s) for s in sims][:3]
        # 模型未按要求返回 similar 字段时，不要回吐原始 JSON，交由下方检索结果兜底

    chunks = (context or {}).get("chunks", [])
    return [
        f"变式：{c['text'][:40]}…（同类题，知识点：{c.get('knowledge_point', '')}）"
        for c in chunks[:3]
    ] or ["[dev] 暂无检索结果，无法生成变式题"]
