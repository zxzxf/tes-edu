"""T-D2 模型路由分级：简单题/直给走小模型，难题走大模型。

判定口径（计划书）：
- ``mode=quick`` / ``mode=similar`` 或题型为**单选/多选** → 小模型；
- **压轴解答**（tier=finale）或 ``mode=explain`` 且步骤数 ≥3 → 大模型；
- 其余（引导档 guide、讲解档但步骤 <3）→ 小模型。

关键约束：

1. **纯函数**：``route_model`` 无 I/O，便于单测逐组合断言（验收要求"六类组合"）。
2. **可配且可关**：模型名取自 ``LLM_MODEL_SIMPLE`` / ``LLM_MODEL_HARD``；
   二者未配（或与 ``LLM_MODEL`` 相同）时**回落单一 ``LLM_MODEL``**，
   行为与接入路由前完全一致——缺配绝不启动失败。
3. **只在缓存未命中后生效**：本模块只决定"用哪个模型"，调用方是
   ``cached_solve`` 包住的远端函数，故路由天然发生在缓存未命中之后
   （T-D1 与 T-D2 的衔接点）。
4. **端点特性已实测**：flash 并发最快但有滚动配额；deepseek 长 prompt 会挂起，
   故**不**把任何长 prompt 档位默认指向 deepseek——默认全部回落 ``LLM_MODEL``。
"""
from __future__ import annotations

from app.core import config, qtype

#: 轻量档位：直给简版与变式题，输出短、不值得上大模型
SIMPLE_MODES = ("quick", "similar")

#: 讲解档进入大模型的步骤数门槛（计划书口径：≥3 步视为多步难题）
HARD_MIN_STEPS = qtype.STEP_GUIDED_MIN_STEPS

GRADE_SIMPLE = "simple"
GRADE_HARD = "hard"
GRADE_SINGLE = "single"  # 未配置分级模型：回落单一模型


def _derive(context: dict | None, tier: str | None, qtype_: str | None, steps):
    """从检索上下文补齐未显式传入的 tier / 题型 / 步骤数（保守：缺则 None/0）。"""
    if tier is None or qtype_ is None or steps is None:
        route = qtype.route_question_tier(context)
        tier = tier if tier is not None else route.get("tier")
        qtype_ = qtype_ if qtype_ is not None else route.get("qtype")
        steps = steps if steps is not None else 0
    return tier, (qtype_ or "").strip(), int(steps or 0)


def route_model(
    *,
    mode: str | None = None,
    context: dict | None = None,
    tier: str | None = None,
    qtype_: str | None = None,
    steps: int | None = None,
    hint_level: int | None = None,
) -> dict:
    """路由纯函数：按 题型 + 难度(tier) + mode 挑模型。

    返回 ``{"model", "grade", "reason", "tier", "qtype", "steps"}``。
    ``grade`` 为 ``simple`` / ``hard`` / ``single``（未启用分级）。
    """
    mode_l = (mode or "").strip().lower()
    tier_v, qtype_v, steps_v = _derive(context, tier, qtype_, steps)

    # 未配置分级模型 -> 单一模型，行为等同接入路由前
    if not config.routing_enabled():
        return {
            "model": config.LLM_MODEL,
            "grade": GRADE_SINGLE,
            "reason": "未配置分级模型（LLM_MODEL_SIMPLE/HARD），回落单一 LLM_MODEL",
            "tier": tier_v,
            "qtype": qtype_v,
            "steps": steps_v,
        }

    # 1) 轻量档位：直给简版 / 变式题
    if mode_l in SIMPLE_MODES:
        return _pick(GRADE_SIMPLE, f"mode={mode_l} 轻量请求", tier_v, qtype_v, steps_v)

    # 2) 压轴解答（题型过程类 + ≥3 步 + 难度 hard）-> 大模型
    if tier_v == qtype.TIER_FINALE:
        return _pick(
            GRADE_HARD,
            f"压轴题（{qtype_v or '解答'}，{steps_v} 步）需完整推理",
            tier_v,
            qtype_v,
            steps_v,
        )

    # 3) 记忆型（单选/多选）-> 小模型（再难的选择题也是记忆型）
    if qtype_v and any(t in qtype_v for t in qtype.MEMORY_TYPES):
        return _pick(GRADE_SIMPLE, f"{qtype_v}为记忆型", tier_v, qtype_v, steps_v)

    # 4) 讲解档且多步 -> 大模型
    if mode_l == "explain" and steps_v >= HARD_MIN_STEPS:
        return _pick(
            GRADE_HARD, f"讲解档且 {steps_v} 步（≥{HARD_MIN_STEPS}）", tier_v, qtype_v, steps_v
        )

    # 5) 其余（引导档 / 少步讲解）-> 小模型
    return _pick(
        GRADE_SIMPLE,
        f"mode={mode_l or 'default'} 步骤 {steps_v}，小模型足够",
        tier_v,
        qtype_v,
        steps_v,
    )


def _pick(grade: str, reason: str, tier, qtype_v, steps_v) -> dict:
    model = config.LLM_MODEL_HARD if grade == GRADE_HARD else config.LLM_MODEL_SIMPLE
    return {
        "model": model,
        "grade": grade,
        "reason": reason,
        "tier": tier,
        "qtype": qtype_v,
        "steps": steps_v,
    }
