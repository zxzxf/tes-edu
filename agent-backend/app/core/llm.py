"""LLM 解答引擎：真实模式调 OpenAI 兼容 /v1/chat/completions（DeepSeek / 通义 / Qwen）。

dev 模式（无 LLM_BASE_URL/KEY）返回"基于真实检索上下文拼装"的讲解而非占位字符串，
保证链路可跑通；生产补上密钥即自动切为真实大模型。
"""
from __future__ import annotations

import json
import logging
import re
import time

from app.core import config, llm_cache, model_router, rag, tenant_llm, usage

log = logging.getLogger("llm")

_SYSTEM = (
    "你是一名高中数学导师，采用苏格拉底式引导讲解。\n"
    "规则：\n"
    "1) 优先依据【检索到的知识点与例题】作答，不得编造；检索内容不足时按学科通法作答。\n"
    "   ⚠️ **禁止在答案里汇报系统内部状态**：不要出现「检索无结果」「无参考材料」"
    "「依据检索到的例题」「材料主要讲解…」这类话，学生看不到也不需要知道检索过程，"
    "直接给数学内容即可。\n"
    "2) mode=explain 讲解优先：先点明考点，再分步骤推导，给完整讲解。\n"
    "3) mode=guide 引导档（默认档）：只点考点 + 给方向 + 给第一步可操作提示，"
    "**不给最终答案、不给完整步骤**；每一步等学生回应后再推进。\n"
    "   ⚠️ 例外（范围很窄）：仅限**看一眼就能报出数**的——如 1+1、2×3、求某个定义、\n"
    "   把 x=3 代入 x+1 求值。直接给答案并补一句要点，不要把简单题拆成数手指式的引导。\n"
    "   ⚠️ **硬性禁止（其余一律不给数值结论）**：凡需先套公式或先变形再计算的题\n"
    "   （含参反解、极值、单调区间、由 Sn 求 an、解析几何联立等），引导档**严禁输出\n"
    "   任何数值结论**——包括「a=0」「a₅=13」「x=3」这类。理由有二：\n"
    "   ① 不给推导过程的数字等于让学生背答案；\n"
    "   ② 实测模型只给方向时会草率心算：曾把「(x+2)/(e^x+a) 最大值1 求a」答成 a=0\n"
    "   （正确 a=1），把「Sn=n²+2n 求 a₅」答成 13（正确 11）。宁可不给，也不能给错。\n"
    "   这类题只说考点、思路方向、第一步做什么，数值等学生自己推或点「直接看解析」。\n"
    "4) mode=quick 给简版直给（学生显式赶时间时才会进该档）；mode=similar 生成 3 道变式题。\n"
    "5) 输出严格 JSON 且不要包裹 markdown：mode=similar 时为 {\"similar\": [\"...\",\"...\",\"...\"]}；\n"
    "   其余模式为 {\"answer\": \"...\", \"steps\": [\"...\"]}。\n"
    "6) **严禁改写题干**：引用题目时必须与【学生问题】逐字一致，分数线、指数、括号、根号\n"
    "   一律照抄。学生常用线性写法（如 x+2/e^x+a），其括号归属以原文为准，\n"
    "   不得自行补括号、不得把 2/e^x 写成 2e^x；确有多种读法时，逐种说明各自的答案。\n"
    "7) **无解要说无解**：若按原文条件该函数在定义域内不存在最大值/最小值，或条件不足以\n"
    "   确定参数，必须明确指出「无解 / 条件不足」并说明原因；绝不允许凑出一个数值答案。\n"
    "8) 含参反解类题目先判断极值的存在性与位置（单调性/定义域），确认存在后再代入列方程。\n"
    "9) **多解必须全列**：解方程得到多个根（如 q⁴=16 得 q=±2、|x|=3 得 x=±3）时，\n"
    "   answer 必须给出**全部**解（写「±2」「2 或 -2」均可），不得只挑一个；\n"
    "   只有当题面条件明确排除掉某些解（如「各项均为正数」「x>0」）才可只留其一。\n"
    "   ⚠️ 实测：等比数列「前4项和4、前8项和68 求公比」，模型默认只给 q=2，\n"
    "   漏掉 q=-2（此时 a₁=-4/5，S₄=4、S₈=68 同样成立）——只给一个解就是错答。"
)


_SLASH_AMBIGUITY_HINT = (
    "\n\n【歧义防空·必读】题干里出现了不带括号的斜杠表达式（形如 A/B+C）。\n"
    "它的分数线覆盖范围未知：既可能是 (A)/(B+C)，也可能是 (A/B)+C —— 这是两个不同的函数，"
    "答案完全不同（例：\\frac{x+2}{e^x+a} 的 a 在分母，若误读成 x+2/e^x+a，a 就跑到分子侧）。\n"
    "因此：① 先明确指出该处存在歧义；② 对两种解读**分别**推导并给出各自的答案；"
    "③ 说明哪一种更可能是原题，并请学生对照原卷确认。\n"
    "严禁只挑一种解读闷头算，也严禁因其中一种解读无解就判定题目出错。"
)


def thinking_kwargs(mode: str | None = None) -> dict:
    """给 ``chat.completions.create`` 补 ``extra_body``，按档位控制推理模型思维链。

    推理模型（如 qwen3.6-flash）默认会先生成几千 token 的思维链再作答，
    实测 33.8s vs 关掉后 5.5s（同题，答案质量不变）。思维链不进答案正文，
    对学生而言纯粹是等待时间，故默认关闭，仅对指定档位开启。

    非推理模型收到该参数会被忽略，不会报错，故可无条件携带。
    """
    on = config.LLM_THINKING or (mode in config.LLM_THINKING_MODES if mode else False)
    if on:
        return {}
    return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}


def _slash_ambiguous(question: str) -> bool:
    """题干是否含"斜杠歧义"（无 LaTeX 分式、斜杠后仍有加减/指数）。

    OCR 把分数线压成 `/` 且丢括号时命中，用于触发两种解读都给的提示。
    """
    import re

    q = question or ""
    if "/" not in q:
        return False
    if "frac" in q or "dfrac" in q:  # 已是规范 LaTeX，无歧义
        return False
    return bool(re.search(r"/\s*[A-Za-z0-9^()\\]+\s*[\+\-]", q))


def _build_messages(question: str, context: dict | None, mode: str) -> list[dict]:
    chunks = (context or {}).get("chunks", [])
    if chunks:
        ctx_text = "\n\n".join(
            f"[例题{i + 1}] {c.get('text', '')}\n知识点：{c.get('knowledge_point', '')}"
            for i, c in enumerate(chunks)
        )
    else:
        # 不要写"请说明检索无结果"——模型会把内部状态复述进答案，
        # 学生看到的就是"检索无结果。依基础运算通法：1+1=2"这种怪话。
        ctx_text = "（无参考材料，直接按学科通法作答）"
    user = f"【检索到的知识点与例题】\n{ctx_text}\n\n【学生问题】\n{question}\n\n【模式】{mode}"
    if _slash_ambiguous(question):
        # 拍照 OCR 常把分数线压成斜杠并丢掉括号：
        # (x+2)/(e^x+a) → x+2/e^x+a，a 从分母跑到分子侧，整道题被换掉。
        # 这种题不能只按一种解读闷头算，必须两种都给，让学生对照原卷确认。
        user += _SLASH_AMBIGUITY_HINT
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


def _salvage_text(text: str) -> str:
    """JSON 解析失败时剥掉外壳，绝不把 ``{"answer": "...`` 原样吐给学生。

    常见成因：输出被 max_tokens 截断（关思考后答案变长更容易撞上限），
    或含未转义字符。此时退回裸文本比给一串 JSON 可读得多。
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`").replace("json", "", 1).strip()
    if not s.startswith("{"):
        return s
    # 先把后续字段（steps/similar…）整段切掉，避免正则越界把 steps 吞进 answer
    s = re.split(r'",\s*"(?:steps|similar|comment|correct)"\s*:', s, maxsplit=1)[0]
    # 结束引号必须"未转义"，否则遇到 \" 会提前截断
    m = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', s, re.S)
    if m:
        try:
            return json.loads('"' + m.group(1) + '"')
        except Exception:  # noqa: BLE001
            return m.group(1).replace('\\"', '"').replace("\\n", "\n")
    # 退而求其次：去掉 JSON 外壳与大括号
    s = re.sub(r'^\{\s*"answer"\s*:\s*"', "", s)
    return s.rstrip('"}').replace('\\"', '"').replace("\\n", "\n")


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
    tier: str | None = None,
    steps: int | None = None,
    store=None,
    tenant_id: str | None = None,
    llm_cfg=None,
) -> dict:
    """解题入口。真实模式经 T-D1 请求级缓存（同题/近似题命中则跳过 LLM）。

    **T-D2 与 T-D1 的衔接**：模型路由发生在 ``_remote_solve`` 内，而它只在缓存
    未命中时才被调用——即"缓存未命中才进路由"。

    W6 BYOM T-M4：``tenant_id`` / ``llm_cfg`` 透传租户级模型配置（解析一次后向下游复用）；
    无租户配置时回落全局 env，行为等同接入 BYOM 前。
    """
    if config.llm_ready():
        cfg = llm_cfg or await tenant_llm.resolve_llm_config(tenant_id)

        async def _call(q, c, m):
            return await _remote_solve(q, c, m, tier=tier, steps=steps, llm_cfg=cfg)

        def _on_cache_hit() -> None:
            # T-D3：缓存命中不烧 LLM，但仍要计数（否则算不出缓存省了多少）
            usage.record_usage_bg(
                mode=mode, subject=subject, cache_hit=True, grade="cache",
                model=cfg.chat_model,
            )

        return await llm_cache.cached_solve(
            _call,
            question,
            context,
            mode,
            subject=subject,
            hint_level=hint_level,
            content_hash=content_hash,
            store=store,
            on_hit=_on_cache_hit,
            embed_cfg=cfg,
            model=cfg.chat_model,
        )
    return _dev_solve(question, context, mode)


async def _remote_solve(
    question: str,
    context: dict | None,
    mode: str,
    *,
    tier: str | None = None,
    steps: int | None = None,
    llm_cfg=None,
) -> dict:
    # W6 BYOM T-M4：租户级配置（chat 段）驱动客户端与模型路由
    cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)
    # T-D2：按题型 + 难度(tier) + mode 选模型（未配分级时回落单一模型；租户可覆盖）
    route = model_router.route_model(
        mode=mode, context=context, tier=tier, steps=steps,
        simple=cfg.model_simple, hard=cfg.model_hard,
    )
    # T-D3：预算超线且动作为 force_simple → 本轮强制降级为小模型（配额节流）
    try:
        bstate = await usage.budget_state()
        usage.audit_budget(bstate)
        if bstate.get("force_simple") and route.get("grade") == model_router.GRADE_HARD:
            route = dict(
                route,
                model=cfg.model_simple,
                grade="hard_forced_simple",
                reason="日预算超线（T-D3），强制降级为小模型",
            )
    except Exception:  # noqa: BLE001 预算查询失败不得影响路由
        pass
    log.info(
        "model route: mode=%s tier=%s steps=%s -> %s (%s) | %s",
        mode,
        route["tier"],
        route["steps"],
        route["model"],
        route["grade"],
        route["reason"],
    )

    client = tenant_llm.make_client(cfg)
    t0 = time.monotonic()
    max_tokens = config.MODE_TO_MAX_TOKENS.get(mode, 800)  # 默认 800
    try:
        resp = await client.chat.completions.create(
            model=route["model"],
            messages=_build_messages(question, context, mode),
            temperature=0.3,
            max_tokens=max_tokens,
            **thinking_kwargs(mode),
        )
        content = resp.choices[0].message.content or ""
        data = _parse_json(content)
        answer = data.get("answer") or _salvage_text(content)
        steps = data.get("steps") or []
        # 不同模型返回形态不一：answer 可能是数字，steps 可能是单个字符串
        if isinstance(steps, str):
            steps = [steps]
        usage.record_usage_bg(
            model=route["model"], grade=route["grade"], mode=mode,
            latency_ms=int((time.monotonic() - t0) * 1000),
            **_tokens(resp),
        )
        return {"answer": str(answer), "steps": [str(s) for s in steps]}
    except Exception as e:  # noqa: BLE001 端点配额耗尽(403)/超时等：降级为基于检索的
        # 通法拼装，不让主链路 500——引导对话宁可给通法，也不能整体失败
        usage.record_usage_bg(
            model=route["model"], grade=route["grade"], mode=mode, degraded=True,
            error_kind=usage.classify_error(e),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return _dev_solve(question, context, mode, degraded=True)


def _tokens(resp) -> dict:
    """从 OpenAI 兼容响应里取 token 计数（不同端点可能缺失 usage）。"""
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return {"prompt_tokens": 0, "completion_tokens": 0}
        return {
            "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
        }
    except Exception:  # noqa: BLE001 token 统计不是主链路，缺失按 0
        return {"prompt_tokens": 0, "completion_tokens": 0}


def _dev_solve(
    question: str, context: dict | None, mode: str, degraded: bool = False
) -> dict:
    """dev / 降级回退入口：拼装模板答案并打 ``degraded`` 标记（T-D3）。

    ``degraded=True`` 时响应带 ``degraded: true`` 与 ``degraded_reason``，
    供 App 提示"当前为降级通法"、埋点与用量表统计降级率。
    """
    out = _dev_answer(question, context, mode, degraded=degraded)
    if degraded:
        out["degraded"] = True
        out["degraded_reason"] = "llm_unavailable"
    return out


def _dev_answer(
    question: str, context: dict | None, mode: str, degraded: bool = False
) -> dict:
    """模板拼装本体：用检索到的真实上下文拼装讲解（非占位字符串）。

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
            "（片段来自题库中的其它题目，其中的数值结论不是本题答案，只作通法参考）\n"
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
    "你是一名严谨的阅卷老师。判断【学生的作答】对于【题目】是否正确。\n"
    "判定规则（按顺序）：\n"
    "1) **先自己独立算出这道题的正确答案**，再与学生作答比对——这是唯一判据。\n"
    "2) 若提供了【参考答案与解析】，可作交叉校验；但若它与题目明显不符"
    "（题目和参考答案讲的不是同一道题），**以你自己算出的结果为准**，不要被带偏。\n"
    "2.1) 但若提供了【系统本轮给出的解答】，那是同一道题、同一题面下系统已经算过的结果，"
    "**优先级高于你自己的重算**：不要因为你的解读与它不同就把学生判错。\n"
    "3) **题面有歧义时（最高优先）**：纯文本题面里的 `A/B+C` 可能原意是分式 "
    "`(A)/(B+C)`（分数线信息在排版中丢失）。若系统解答已列出多种解读"
    "（形如「若为…则…；若为…则…」），**学生作答只要命中其中任意一种解读下的正确答案，"
    "即判 correct=true**；不得只按你自己选的那一种解读去否定学生。\n"
    "4) 学生作答只要结论正确即算 correct，**不要因为没写过程就判错**——"
    "只有题目本身明确要求写出推导/证明过程时才把过程纳入判定。\n"
    "5) 表述差异不影响判定：「等于2」「2」「答案为2」「结果是2」视为同一作答。\n"
    "6) 结论对但关键过程明显错误算 partial；结论错误才算 correct=false。\n"
    "7) 学生作答与题目完全无关、或你无法确定对错时，correct 填 false 并在 comment 说明原因。\n"
    "只输出严格 JSON 且不要包裹 markdown："
    '{"correct": true/false, "partial": true/false, "comment": "一句话点评"}。'
)


_KV_RE = re.compile(
    r"(?<![\w\\])([A-Za-z一-鿿][A-Za-z0-9_]{0,5}(?:\([^)]{1,12}\))?)\s*[=＝]\s*"
    r"([^\s,，;；。、()（）]{1,24})"
)


def _kv_pairs(text: str) -> dict[str, str]:
    """抽出答案里的「标识符=值」对，用于判分兜底比对。

    例：``a=1``、``a_5=11``、``S_n=2n-1``、``f(x)=...``。学生的 ``a=1`` 与系统
    解答里的 ``a=1`` 能精确对上，这是模型重算失败时最可靠的一道保险。
    """
    out: dict[str, str] = {}
    for k, v in _KV_RE.findall(text or ""):
        out[_norm(k)] = _norm(v.rstrip("。.，,"))
    return out


def _reconcile_with_ref(
    verdict: dict, student_answer: str, ref_answer: str | None
) -> dict:
    """红线兜底：模型重算把**与系统解答一致**的作答判成错时，改判正确。

    ⚠️ 为什么要这道闸（2026-10-29 线上事故）：题干 OCR 成 ``f(x)=x+2/e^x+a``，
    原意实为分式 ``(x+2)/(e^x+a)``（答案 a=1）。系统 solve 侧已识别歧义、给出
    「若为…则 a=1」的双解读；但判分让模型**自己重算一遍**，模型按字面解读算出
    a=-ln2（且 comment 里自相矛盾地写了三个不同值），把学生正确的 a=1 判为错误。

    模型重算在歧义题上不可靠，而「系统本轮给出的解答」是同源同题、且已做过歧义
    处理的结果——两者的「标识符=值」精确一致时，学生的答案就是对的，不该被推翻。
    只在模型判 False 时才介入（判 True 无需纠正），改判同时留痕说明。
    """
    if not ref_answer or verdict.get("correct") is not False:
        return verdict
    stu = _kv_pairs(student_answer)
    if not stu:
        return verdict
    ref = _kv_pairs(ref_answer)
    hit = {k: v for k, v in stu.items() if k in ref and ref[k] == v}
    if not hit:
        return verdict
    verdict["correct"] = True
    verdict["partial"] = False
    verdict["comment"] = (
        "作答与系统解答一致（" + "、".join(f"{k}={v}" for k, v in hit.items()) + "）；"
        "本题题面存在排版歧义，已按系统给出的解读判定。"
    )
    return verdict


def _norm(s: str) -> str:
    """兜底比对用的归一化：去空白与常见标点。"""
    import re

    s = (s or "").strip().lower()
    return re.sub(r"[\s，,。.;；:：、()（）\[\]{}]+", "", s)


async def judge(
    question: str,
    student_answer: str,
    context: dict | None = None,
    *,
    tenant_id: str | None = None,
    llm_cfg=None,
    ref_answer: str | None = None,
) -> dict:
    """判定学生作答。真实模式交给 LLM；否则用检索到的参考答案做兜底比对。

    W6 BYOM T-M4：``tenant_id`` / ``llm_cfg`` 透传租户级模型配置。

    ``ref_answer`` 是本轮 solve 已经算出的系统解答（可能含歧义题的多解读）。
    判分时一并喂给模型并做一致性兜底，避免模型换个解读重算后冤枉学生。
    """
    if config.llm_ready():
        return await _remote_judge(
            question, student_answer, context, llm_cfg=llm_cfg,
            ref_answer=ref_answer, tenant_id=tenant_id,
        )
    return _dev_judge(student_answer, context)


async def _remote_judge(
    question: str,
    student_answer: str,
    context: dict | None,
    *,
    llm_cfg=None,
    ref_answer: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    # ⚠️ 此前写死 resolve_llm_config(None)（平台全局），tenant_id 收了却不用：
    # 主解走租户模型、判分走全局模型，同一份歧义题面两个模型解读不同，
    # 就会出现"系统自己答 1、判分说 1 错"。判分必须与主解用同一套配置。
    cfg = llm_cfg or await tenant_llm.resolve_llm_config(tenant_id)

    # ⚠️ 只取达到相似度门槛的可信 chunk（见 rag.reliable_chunks）：检索永远返回
    # TOP_K 条，库外题（如「1+1等于」）会硬凑出复数方程真题当"参考答案"，
    # 拿着错题的答案判分必然误判。过滤后无参考时，提示词明确要求按题目本身判。
    chunks = rag.reliable_chunks(context)
    ref = "\n".join(
        f"[参考{i + 1}] 答案：{c.get('answer', '')} 解析：{c.get('text', '')[:120]}"
        for i, c in enumerate(chunks[:3])
    ) or (
        "（题库未命中此题：请**只依据题目本身的数学/学科事实**判断作答对错，"
        "不要因为缺少参考答案就判错）"
    )
    # 系统本轮已算出的解答：同源同题且做过歧义处理，模型不得换个解读把它推翻
    sysref = (
        f"\n\n【系统本轮给出的解答】（同一题面，优先级最高，含歧义题的多解读）\n{ref_answer}"
        if ref_answer
        else ""
    )
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"【题目】\n{question}\n\n【参考答案与解析】\n{ref}"
                f"{sysref}\n\n【学生的作答】\n{student_answer}"
            ),
        },
    ]
    t0 = time.monotonic()
    try:
        client = tenant_llm.make_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.chat_model, messages=messages, temperature=0,
            **thinking_kwargs("judge"),
        )
        data = _parse_json(resp.choices[0].message.content or "")
        if isinstance(data.get("correct"), bool):
            usage.record_usage_bg(
                model=cfg.chat_model, grade="single", mode="judge",
                latency_ms=int((time.monotonic() - t0) * 1000), **_tokens(resp),
            )
            return _reconcile_with_ref(
                {
                    "correct": data["correct"],
                    "partial": bool(data.get("partial", False)),
                    "comment": str(data.get("comment") or ""),
                },
                student_answer,
                ref_answer,
            )
    except Exception as e:  # noqa: BLE001 判分失败降级为兜底比对，不影响主链路
        usage.record_usage_bg(
            model=cfg.chat_model, grade="single", mode="judge", degraded=True,
            error_kind=usage.classify_error(e),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    return _reconcile_with_ref(_dev_judge(student_answer, context), student_answer, ref_answer)


def _dev_judge(student_answer: str, context: dict | None) -> dict:
    """无 LLM 时的兜底：与检索到的参考答案做归一化字符串比对。

    同样只认可信 chunk（rag.reliable_chunks）——否则库外题会拿错题的答案比对。
    且「没有任何可信参考」时返回 correct=None（无法判定），**不是 False**：
    没依据就判错会污染掌握度、误记错题，宁可留空转人工。
    """
    target = _norm(student_answer)
    if not target:
        return {"correct": False, "partial": False, "comment": "未提交作答"}
    chunks = rag.reliable_chunks(context)
    if not chunks:
        return {"correct": None, "partial": False, "comment": "题库未命中，无参考答案可比对，转人工复核"}
    for c in chunks:
        ref = _norm(c.get("answer", ""))
        if ref and (ref == target or ref in target or target in ref):
            return {"correct": True, "partial": False, "comment": "与参考答案一致（兜底比对）"}
    return {"correct": False, "partial": False, "comment": "与参考答案不一致（兜底比对）"}


async def suggest_similar_detailed(
    question: str, context: dict | None = None, *, tenant_id: str | None = None, llm_cfg=None
) -> dict:
    """变式题生成（带降级标记）：``{"similar": [...], "degraded": bool}``。

    T-D3：变式是旁路能力，LLM 配额打满时曾把整轮 Hint 对话打成 500（T-C2 实测），
    故这里失败静默回落检索结果，但**必须把 degraded 标记回传**，
    让埋点与用量表能统计"变式降级率"——否则降级是隐形的。

    W6 BYOM T-M4：``tenant_id`` / ``llm_cfg`` 透传租户级模型配置。

    T-D1 补位（2026-10-27）：此前变式**裸调 LLM 未接缓存**，同题每次都重烧 7~17s，
    是解题链路最大的一块无谓开销。现与 ``solve`` 同口径走 ``cached_solve``
    （``mode="similar"`` 独立命名空间，不与主解/引导档互串）；
    ``_remote_similar`` 生成失败或没解析出变式时返回 ``None``——**不入缓存**，
    下次重新尝试，避免把"没生成出来"固化成 7 天缓存。
    """
    if config.llm_ready():
        cfg = llm_cfg or await tenant_llm.resolve_llm_config(tenant_id)

        def _on_cache_hit() -> None:
            # T-D3：缓存命中不烧 LLM，但仍计数（否则算不出缓存省了多少）
            usage.record_usage_bg(
                mode="similar", cache_hit=True, grade="cache", model=cfg.chat_model,
            )

        async def _call(q, ctx, _mode):
            return await _remote_similar(q, ctx, llm_cfg=cfg)

        cached = await llm_cache.cached_solve(
            _call, question, context, "similar",
            on_hit=_on_cache_hit, embed_cfg=cfg, model=cfg.chat_model,
        )
        if cached is not None:
            return cached

    chunks = (context or {}).get("chunks", [])
    return {
        # 波 1 实测：额度耗尽时这里给出的是检索片段（截断），不是可练的完整题目。
        # 文案不再自称"变式"（避免 App/埋点把它当真题统计），改称"片段"。
        "similar": [
            f"相似题片段：{c['text'][:40]}…（知识点：{c.get('knowledge_point', '')}）"
            for c in chunks[:3]
        ] or ["[dev] 暂无检索结果，无法生成变式题"],
        # 只有"配了 LLM 却没生成出来"才算降级；dev（无密钥）本就是模板模式，不计
        "degraded": bool(config.llm_ready()),
    }


async def suggest_similar(question: str, context: dict | None = None) -> list[str]:
    """兼容旧签名：只返回变式题列表（T-D3 起内部走 detailed 版本）。"""
    return (await suggest_similar_detailed(question, context))["similar"]


async def _remote_similar(
    question: str, context: dict | None, *, llm_cfg=None
) -> dict | None:
    """真实调用 LLM 生成变式题。成功返回 ``{"similar": [...], "degraded": False}``；
    失败 / 模型没按格式返回时返回 ``None``（调用方据此回落检索片段，且**不进缓存**）。
    """
    cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)
    t0 = time.monotonic()
    route = model_router.route_model(
        mode="similar", context=context, simple=cfg.model_simple, hard=cfg.model_hard
    )
    try:
        client = tenant_llm.make_client(cfg)
        max_tokens = config.MODE_TO_MAX_TOKENS.get("similar", 400)
        resp = await client.chat.completions.create(
            model=route["model"],
            messages=_build_messages(question, context, "similar"),
            temperature=0.7,
            max_tokens=max_tokens,
            **thinking_kwargs("similar"),
        )
        content = resp.choices[0].message.content or ""
        data = _parse_json(content)
        sims = data.get("similar")
        if isinstance(sims, str):
            sims = [sims]
        if sims:
            usage.record_usage_bg(
                model=route["model"], grade=route["grade"], mode="similar",
                latency_ms=int((time.monotonic() - t0) * 1000), **_tokens(resp),
            )
            return {"similar": [str(s) for s in sims][:3], "degraded": False}
        # 模型未按要求返回 similar 字段时，不要回吐原始 JSON，交由下方检索结果兜底
    except Exception as e:  # noqa: BLE001 变式是旁路能力：LLM 配额耗尽/超时不得
        # 影响主链路（T-C2 实测：similar 的 403 曾把整轮 Hint 对话打成 500）
        usage.record_usage_bg(
            model=route["model"], grade=route["grade"], mode="similar", degraded=True,
            error_kind=usage.classify_error(e),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    return None
