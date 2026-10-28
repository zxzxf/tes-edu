"""Orchestrator：意图路由 + 工具编排（P0 骨架）。

P0 阶段只打通链路：RAG 检索 -> LLM 解题 -> 触类旁通。
具体 LLM / 向量库 / OCR 实现见同目录各模块，P0 用占位返回，便于前后端先联调。

P2（T-C1）起增加意图三档路由：学生请求自动进 直给/讲解优先/引导 三档，
不再依赖调用方传 mode；分类失败显式降级引导档，不静默直给（见 intent.py）。

P2（T-C2）起增加 Hint 阶梯对话：``hint=True`` 时按 (学生, 题) 维护 L0–L4
阶梯状态（socratic.py），挫败连击跳级、压轴题封顶 L3、"直接看解析"直达 L4。

P2（T-C3）起增加题型路由（qtype.py）：记忆型直接讲解 / 中等引导 1-2 轮 /
压轴引导 3-4 轮并按 course_kb_steps 分步拆解；``mode=direct`` 是一等功能
"直接看解析"的服务端入口（直达 L4 + 复盘三问，行为入埋点不作违规）。
"""
from __future__ import annotations

import asyncio
import time

from app.core import config, intent, judge_v2, llm, ocr, qtype, rag, socratic, tenant_llm

# ---------------------------------------------------------------- bundle 缓存

#: 题目 bundle（type + steps）的进程内缓存：压轴题分步引导每轮都要 steps，
#: 同题多轮复用，避免每轮两次查询。TTL 10 分钟，容量 256（会话局部性）。
_BUNDLE_CACHE: dict[str, tuple[float, dict]] = {}
_BUNDLE_CACHE_TTL = 600.0
_BUNDLE_CACHE_MAX = 256


async def _load_bundle_cached(content_hash: str | None) -> dict | None:
    """取题目 bundle（judge_v2.load_bundle），带 TTL 缓存。"""
    if not content_hash:
        return None
    now = time.monotonic()
    hit = _BUNDLE_CACHE.get(content_hash)
    if hit and now - hit[0] < _BUNDLE_CACHE_TTL:
        return hit[1]
    bundle = await judge_v2.load_bundle(content_hash)
    if bundle:
        _BUNDLE_CACHE[content_hash] = (now, bundle)
        if len(_BUNDLE_CACHE) > _BUNDLE_CACHE_MAX:
            oldest = next(iter(_BUNDLE_CACHE))
            if oldest != content_hash:
                del _BUNDLE_CACHE[oldest]
    return bundle


def _tier_fields(route: dict) -> dict:
    """题型路由回传字段（App 展示 + T-C4 埋点共用）。"""
    return {
        "question_tier": route["tier"],
        "question_tier_name": route["tier_name"],
        "question_type": route["qtype"],
        "hint_max_rounds": route["max_rounds"],
    }


async def handle_question(
    question: str,
    mode: str | None = None,
    subject: str | None = None,
    student_answer: str | None = None,
    hint: bool = False,
    student_reply: str | None = None,
    direct_reveal: bool = False,
    student_id: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    # 0) W6 BYOM T-M4：解析租户级模型配置（命中租户行且 enabled → 解密；否则回落全局 env）。
    #    一次请求内只解析一次，向下游所有 LLM 调用透传，避免重复查库/缓存。
    cfg = await tenant_llm.resolve_llm_config(tenant_id) if tenant_id else None
    # 1) RAG 检索上下文（知识点 / 例题 / 题型）
    #    llm_cfg（租户级配置）透传：学校配置了独立 embedding 模型时按学校端点向量化
    ctx = await rag.retrieve(question, subject=subject, llm_cfg=cfg)
    # 2) T-C3 "直接看解析"一等功能：mode=direct 直达 L4（等价 direct_reveal），
    #    App 常驻按钮走此入口；行为入埋点（solve.py）且不作为违规（R6）。
    if mode == "direct":
        direct_reveal = True
        hint = True
    # 3) T-C2：Hint 阶梯对话轮（优先于三档路由——阶梯只存在于引导档）
    if hint:
        return await _handle_hint_round(
            question,
            ctx,
            subject,
            student_answer=student_answer,
            student_reply=student_reply,
            direct_reveal=direct_reveal,
            student_id=student_id,
            llm_cfg=cfg,
        )
    # 4) 意图三档路由（T-C1）：
    #    - 显式传 mode（含 /similar 的 similar）-> 尊重调用方，intent 不分类；
    #    - 未传 -> 规则分类 -> LLM 兜底 -> 默认引导档（不静默直给）。
    if mode:
        intent_tier, resolved_mode, intent_source = None, mode, "explicit"
    else:
        intent_tier, resolved_mode, intent_source = await intent.resolve_response_mode(
            question, ctx, llm_cfg=cfg
        )
    # 5) LLM 解题 + 分步讲解（按档位生成）；T-D1 请求级缓存（键含学科/mode/hint_level）
    #    T-D2：题型档位与步骤数透传给模型路由（缓存未命中才真正进路由）
    _route_tier = qtype.route_question_tier(ctx)
    # 6) 触类旁通（T-D3：变式降级单独标记，不污染主解 degraded 语义）
    #    ⚠️ 与主解**并行**：实测两次调用各约 18-20s，串行会让 P95 翻倍到 ~39s。
    #    两者互不依赖（similar 只吃 question/ctx），gather 后总耗时取较慢的那个。
    if config.SIMILAR_ON_SOLVE:
        result, sim = await asyncio.gather(
            llm.solve(
                question,
                context=ctx,
                mode=resolved_mode,
                subject=subject,
                hint_level=None,
                content_hash=judge_v2._pick_content_hash(ctx),
                tier=_route_tier.get("tier"),
                steps=len(ctx.get("steps") or []),
                llm_cfg=cfg,
            ),
            llm.suggest_similar_detailed(question, context=ctx, llm_cfg=cfg),
        )
    else:
        # 端点慢/预算紧时可整体关掉变式生成（环境变量 SIMILAR_ON_SOLVE=0），
        # 主解照常返回，similar 置空——App 侧"触类旁通"为空即不展示。
        result = await llm.solve(
            question,
            context=ctx,
            mode=resolved_mode,
            subject=subject,
            hint_level=None,
            content_hash=judge_v2._pick_content_hash(ctx),
            tier=_route_tier.get("tier"),
            steps=len(ctx.get("steps") or []),
            llm_cfg=cfg,
        )
        sim = {"similar": [], "degraded": False}
    out = {
        **result,
        "similar": sim["similar"],
        "similar_degraded": sim["degraded"],
        # T-D3：主解是否降级（模板通法）。埋点与 App 提示都读这个字段
        "degraded": bool(result.get("degraded")),
        "knowledge_points": ctx.get("points", []),
        # 命中的标注题（步骤级归因的归属对象），事件上报与前端核对都用它
        "content_hash": judge_v2._pick_content_hash(ctx) or "",
        # T-C1：三档路由结果回传——埋点（T-C4）与 App 展示"当前为引导档/直给档"用。
        # intent 仅在实际跑了分类时有值；explicit 模式（如 similar）为 None。
        "intent": intent_tier,
        "mode": resolved_mode,
        "intent_source": intent_source,
        # T-C3：题型路由（chunks 轻量信号，不查库）——App 决定是否展示
        # "直接看解析"常驻按钮与阶梯入口；埋点按题型分层聚合
        **_tier_fields(qtype.route_question_tier(ctx)),
    }
    # 7) 学生提交了自己的作答 -> judge v2 步骤级归因判分
    #    客观题/填空走查表（零 token），解答题走 LLM；都不行才 unknown。
    #    判据用 `is not None`：字段出现即表示"提交了作答"，
    #    空串是"交了白卷"，必须回 need_process 引导，而不是静默不判分。
    if student_answer is not None:
        out["judgement"] = await _judge_v2_with_fallback(
            question,
            student_answer,
            ctx,
            llm_cfg=cfg,
            # 本轮 solve 已算出的答案作判据：歧义题（如 OCR 压掉分数线的
            # "x+2/e^x+a"）若让判分模型换个解读重算，必然把答对的学生判错。
            ref_answer=str(result.get("answer") or ""),
        )
    return out


async def _handle_hint_round(
    question: str,
    ctx: dict,
    subject: str | None,
    *,
    student_answer: str | None,
    student_reply: str | None,
    direct_reveal: bool,
    student_id: str | None,
    llm_cfg=None,
) -> dict:
    """T-C2/T-C3：Hint 阶梯的一轮对话。

    流程：题型路由（qtype）-> 取阶梯状态 -> 挫败信号累计 -> decide_level 定级 ->
    （压轴题）按 course_kb_steps 分步推进 -> 判分（提交了作答时）->
    generate_hint 生成（LLM 优先，违约/失败回落模板）-> 状态落库。
    阶梯只存在于引导档（mode=guide），直给档不进阶梯。
    """
    state = socratic.ladder.get(student_id, question)
    # 挫败信号：本轮回应是挫败表达则连击 +1，否则清零（"连续 2 次"的"连续"）
    streak = state["streak"] + 1 if socratic.is_frustration_signal(student_reply) else 0

    # T-C3 题型路由：先用 chunks 轻量信号定档；疑似压轴再查库取 steps 精确定档
    route = qtype.route_question_tier(ctx)
    bundle = None
    content_hash = judge_v2._pick_content_hash(ctx) or ""
    if route["tier"] != qtype.TIER_MEMORY and content_hash:
        bundle = await _load_bundle_cached(content_hash)
        route = qtype.route_question_tier(ctx, bundle)
    finale = route["tier"] == qtype.TIER_FINALE

    level = socratic.decide_level(
        state["level"], streak, direct_reveal=direct_reveal, finale=finale
    )

    # 分步拆解：压轴题按 course_kb_steps 顺序推进，一步一 Hint。
    # 先判推进信号再生成本轮 Hint——学生回应当轮的 Hint 面向**下一步**
    # （他回应的就是当前步，再给当前步的 Hint 是空转）。
    step = None
    step_idx = int(state.get("step") or 0)

    # 阶梯轮中提交了作答 -> judge v2（答对是该轮最大的"推进"信号）
    judgement = None
    if student_answer is not None:
        judgement = await _judge_v2_with_fallback(question, student_answer, ctx, llm_cfg=llm_cfg)

    if finale and bundle:
        total_steps = len(bundle.get("steps") or [])
        if qtype.step_progress_signal(judgement, student_reply, streak):
            step_idx = min(step_idx + 1, total_steps - 1)
        step = qtype.step_guidance(bundle, step_idx)
        if step:
            step_idx = step["step_no"] - 1

    # 对话历史：进程内只保留最近一轮学生回应（完整历史由 App 侧维护）
    history = [student_reply] if student_reply else None
    hint = await socratic.generate_hint(
        question, ctx, level, history, step,
        subject=subject, content_hash=content_hash, tier=route.get("tier"),
        llm_cfg=llm_cfg,
    )

    socratic.ladder.record_round(
        student_id,
        question,
        level=level,
        streak=streak,
        step=step_idx if step else None,
    )
    sim = await llm.suggest_similar_detailed(question, context=ctx, llm_cfg=llm_cfg)
    out = {
        **hint,
        "similar": sim["similar"],
        "similar_degraded": sim["degraded"],
        # T-D3：本轮 Hint 是否由模板回落（LLM 挂了或输出违约）
        "degraded": bool(hint.get("degraded")),
        "knowledge_points": ctx.get("points", []),
        "content_hash": content_hash,
        # 阶梯轮次固定引导档；intent_source=hint_ladder 与三档路由的来源区分
        "intent": intent.INTENT_GUIDE,
        "mode": "guide",
        "intent_source": "hint_ladder",
        # T-C2 阶梯回传：App 展示当前等级名，埋点（T-C4）聚合推进率/挫败率
        "hint_level": level,
        "hint_level_name": socratic.LEVEL_NAMES[level],
        "hint_frustration_streak": streak,
        "hint_finale": finale,
        "hint_direct_reveal": direct_reveal,
        "hint_rounds": state.get("rounds", 0) + 1,
        # T-C3 题型路由回传：轮次上限 / 分步进度（App 展示"第 N/M 步"）
        **_tier_fields(route),
    }
    if step:
        out["hint_step"] = step["step_no"]
        out["hint_step_total"] = step["total"]
        out["hint_step_text"] = step["text"]
    if judgement is not None:
        out["judgement"] = judgement
    return out


async def _judge_v2_with_fallback(
    question: str,
    student_answer: str,
    ctx: dict,
    *,
    llm_cfg=None,
    ref_answer: str | None = None,
) -> dict:
    """judge v2 主判；低置信（unknown）时退回 v1 的对错布尔，保住原有信号。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。

    ``ref_answer`` 是本轮 solve 的答案，作为 v1 兜底判分的判据与红线校验依据。
    """
    j = await judge_v2.judge(question, student_answer, context=ctx, llm_cfg=llm_cfg)
    # S4：被要求补过程时不要退回 v1 —— 那会白花一次 LLM 且可能把"没写过程"判成"做错"
    if j.get("need_process"):
        return j
    if j.get("attribution_source") == judge_v2.UNKNOWN:
        v1 = await llm.judge(
            question, student_answer, context=ctx, llm_cfg=llm_cfg, ref_answer=ref_answer
        )
        if isinstance(v1.get("correct"), bool):
            j["correct"] = v1["correct"]
            j["partial"] = bool(v1.get("partial", False))
            # unknown 分支自带的 comment 是「未匹配到标注题，转人工复核」；v1 已判出
            # 结论时必须换成 v1 的说法，否则会回出「correct=false + 转人工复核」这种
            # 自相矛盾的包，学生看到的是"答错了"而不是"没判出来"。
            if v1.get("comment"):
                j["comment"] = str(v1["comment"])
    return j


async def handle_image(image_bytes: bytes, *, tenant_id: str | None = None) -> dict:
    """拍照：OCR 抽题面 -> 文本题链路。

    与 `similar_degraded` 同口径：不可用时**显式回 degraded/vlm_enabled**，
    绝不静默降级。两个短路点：

      1. 视觉模型未配置 —— 按租户从 BYOM 解析 VLM 配置（复用学校 chat 段 api_key），
         回落平台全局 VLM；两者皆无则直接短路，不消耗 LLM 配额，也不把降级文案
         当题面送进 handle_question（早期实现会因此生成牛头不对马嘴的"解析"）。
      2. OCR 返回空白 —— 图片没拍到题目/全白，同样不值得进 LLM。
    """
    cfg = await tenant_llm.resolve_llm_config(tenant_id) if tenant_id else tenant_llm._global_config()
    if not tenant_llm.vlm_enabled(cfg):
        return {
            "answer": "",
            "steps": [],
            "similar": [],
            "knowledge_points": [],
            "recognized_text": "",
            "vlm_enabled": False,
            "degraded": True,
            "message": (
                "拍照识题未启用：该租户未配置视觉模型"
                "（请在 BYOM 后台填写 vlm_model，密钥复用 Chat 段）。请先用文字输入。"
            ),
        }

    text = await ocr.recognize(
        image_bytes,
        base_url=cfg.vlm_base_url,
        api_key=cfg.chat_api_key,
        model=cfg.vlm_model,
    )
    if not text.strip():
        return {
            "answer": "",
            "steps": [],
            "similar": [],
            "knowledge_points": [],
            "recognized_text": "",
            "vlm_enabled": True,
            "degraded": True,
            "message": "没从这张图里识别出文字，换一张拍清楚些的，或改用文字输入。",
        }

    result = await handle_question(text, tenant_id=tenant_id)
    # 回传识别文本，供学情上报与前端核对（文本题链路不返回该字段）
    result["recognized_text"] = text
    result["vlm_enabled"] = True
    result["degraded"] = False
    return result
