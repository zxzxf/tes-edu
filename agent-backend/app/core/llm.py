"""LLM 解答引擎：真实模式调 OpenAI 兼容 /v1/chat/completions（DeepSeek / 通义 / Qwen）。

dev 模式（无 LLM_BASE_URL/KEY）返回"基于真实检索上下文拼装"的讲解而非占位字符串，
保证链路可跑通；生产补上密钥即自动切为真实大模型。
"""
from __future__ import annotations

import json

from app.core import config, llm_cache

_SYSTEM = (
    "你是一名高中数学导师，采用苏格拉底式引导讲解。\n"
    "规则：\n"
    "1) 必须依据【检索到的知识点与例题】作答，不得编造；检索内容不足时先说明并给出通法。\n"
    "2) mode=explain 讲解优先：先点明考点，再分步骤推导，给完整讲解。\n"
    "3) mode=guide 引导档（默认档）：只点考点 + 给方向 + 给第一步可操作提示，"
    "**不给最终答案、不给完整步骤**；每一步等学生回应后再推进。\n"
    "4) mode=quick 给简版直给（学生显式赶时间时才会进该档）；mode=similar 生成 3 道变式题。\n"
    "5) 输出严格 JSON 且不要包裹 markdown：mode=similar 时为 {\"similar\": [\"...\",\"...\",\"...\"]}；\n"
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


async def solve(
    question: str,
    context: dict | None = None,
    mode: str = "explain",
    *,
    subject: str | None = None,
    hint_level: int | None = None,
    content_hash: str | None = None,
    store=None,
) -> dict:
    """解题入口。真实模式经 T-D1 请求级缓存（同题/近似题命中则跳过 LLM）。"""
    if config.llm_ready():
        return await llm_cache.cached_solve(
            _remote_solve,
            question,
            context,
            mode,
            subject=subject,
            hint_level=hint_level,
            content_hash=content_hash,
            store=store,
        )
    return _dev_solve(question, context, mode)


async def _remote_solve(question: str, context: dict | None, mode: str) -> dict:
    from openai import AsyncOpenAI  # 延迟导入

    client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    try:
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
    except Exception:  # noqa: BLE001 端点配额耗尽(403)/超时等：降级为基于检索的
        # 通法拼装，不让主链路 500——引导对话宁可给通法，也不能整体失败
        return _dev_solve(question, context, mode, degraded=True)


def _dev_solve(
    question: str, context: dict | None, mode: str, degraded: bool = False
) -> dict:
    """dev 回退：用检索到的真实上下文拼装讲解（非占位字符串）。

    ``degraded=True`` 表示生产环境 LLM 不可用（配额耗尽/超时）后的降级，
    文案与无密钥 dev 模式区分，避免误导排查。
    """
    chunks = (context or {}).get("chunks", [])
    points = list(dict.fromkeys(c.get("knowledge_point") for c in chunks if c.get("knowledge_point")))
    point_text = "、".join(points) if points else "待标注（检索未命中，需补题）"

    if mode == "quick":
        first = chunks[0]["text"][:60] if chunks else question
        return {
            "answer": f"【简版直给】考点：{point_text}。通法要点：{first}",
            "steps": ["直接给出关键步骤", "套用通法求解"],
        }

    if mode == "guide":
        # 引导档（T-C1）：点考点 + 方向 + 第一步提示；**不给最终答案**。
        # 首轮 Hint 即 T-C2 阶梯的 L0-L1；多轮阶梯对话（等级状态/跳级/
        # 直接看解析）由 socratic.py 维护，入口为 /solve 的 hint=true。
        first = chunks[0]["text"][:60] if chunks else question
        return {
            "answer": (
                f"【引导档】这道题考的是：{point_text}。\n"
                f"先别急着算——我们只看第一步：{first}\n"
                "你先说说这一步打算怎么下手？说个思路就行，不用算完。"
            ),
            "steps": [
                f"第一步：从「{point_text}」入手，明确已知条件和所求量",
                "说出你的第一步思路（不用完整计算）",
                "根据你的回应，我再决定下一步给多少提示",
            ],
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
    if degraded:
        # 生产降级（LLM 配额耗尽/超时）：明示是降级内容，而非 dev 占位
        answer = (
            f"[降级回退] 模型暂不可用（配额/超时），以下基于检索到的 {len(chunks)} 条同类例题给出通法：\n"
            f"{evidence}\n"
            f"本题建议从「{point_text}」入手，先明确条件再套通法。"
        )
    else:
        answer = (
            f"[dev-无密钥回退] 已检索到 {len(chunks)} 条相关例题：\n{evidence}\n"
            f"本题建议从「{point_text}」入手，先明确条件再套通法。"
            f"配置 LLM_BASE_URL / LLM_API_KEY 后由大模型生成完整讲解。"
        )
    return {"answer": answer, "steps": steps}


_JUDGE_SYSTEM = (
    "你是一名高中数学阅卷老师。请依据【参考答案与解析】判断【学生的作答】是否正确。\n"
    "判定标准：结论与关键过程都对才算 correct；结论对但过程明显错误算 partial；否则算错误。\n"
    "只输出严格 JSON 且不要包裹 markdown："
    '{"correct": true/false, "partial": true/false, "comment": "一句话点评"}。'
)


def _norm(s: str) -> str:
    """兜底比对用的归一化：去空白与常见标点。"""
    import re

    s = (s or "").strip().lower()
    return re.sub(r"[\s，,。.;；:：、()（）\[\]{}]+", "", s)


async def judge(
    question: str,
    student_answer: str,
    context: dict | None = None,
) -> dict:
    """判定学生作答。真实模式交给 LLM；否则用检索到的参考答案做兜底比对。"""
    if config.llm_ready():
        return await _remote_judge(question, student_answer, context)
    return _dev_judge(student_answer, context)


async def _remote_judge(question: str, student_answer: str, context: dict | None) -> dict:
    from openai import AsyncOpenAI  # 延迟导入

    chunks = (context or {}).get("chunks", [])
    ref = "\n".join(
        f"[参考{i + 1}] 答案：{c.get('answer', '')} 解析：{c.get('text', '')[:120]}"
        for i, c in enumerate(chunks[:3])
    ) or "（无参考答案，按通法判断）"
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {
            "role": "user",
            "content": f"【题目】\n{question}\n\n【参考答案与解析】\n{ref}\n\n【学生的作答】\n{student_answer}",
        },
    ]
    try:
        client = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL, messages=messages, temperature=0
        )
        data = _parse_json(resp.choices[0].message.content or "")
        if isinstance(data.get("correct"), bool):
            return {
                "correct": data["correct"],
                "partial": bool(data.get("partial", False)),
                "comment": str(data.get("comment") or ""),
            }
    except Exception:  # noqa: BLE001 判分失败降级为兜底比对，不影响主链路
        pass
    return _dev_judge(student_answer, context)


def _dev_judge(student_answer: str, context: dict | None) -> dict:
    """无 LLM 时的兜底：与检索到的参考答案做归一化字符串比对。"""
    target = _norm(student_answer)
    if not target:
        return {"correct": False, "partial": False, "comment": "未提交作答"}
    for c in (context or {}).get("chunks", []):
        ref = _norm(c.get("answer", ""))
        if ref and (ref == target or ref in target or target in ref):
            return {"correct": True, "partial": False, "comment": "与参考答案一致（兜底比对）"}
    return {"correct": False, "partial": False, "comment": "与参考答案不一致（兜底比对）"}


async def suggest_similar(question: str, context: dict | None = None) -> list[str]:
    if config.llm_ready():
        try:
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
        except Exception:  # noqa: BLE001 变式是旁路能力：LLM 配额耗尽/超时不得
            # 影响主链路（T-C2 实测：similar 的 403 曾把整轮 Hint 对话打成 500）
            pass

    chunks = (context or {}).get("chunks", [])
    return [
        f"变式：{c['text'][:40]}…（同类题，知识点：{c.get('knowledge_point', '')}）"
        for c in chunks[:3]
    ] or ["[dev] 暂无检索结果，无法生成变式题"]
