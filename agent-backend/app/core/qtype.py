"""P2 引导式对话 —— 题型路由与分步拆解策略（T-C3）。

四类题型 -> 响应策略（与分期书/计划书口径一致）：

- 记忆型（单选/多选）        -> 直接讲解，引导轮次上限 1（记忆题不值得苏格拉底式绕弯）
- 中等（填空 / 解答非压轴）  -> 引导 1-2 轮，轮次上限 2
- 压轴（解答类 + ≥3 步 + 难度 hard）-> 引导 3-4 轮 + 按 ``course_kb_steps``
  分步拆解（一步一 Hint），轮次上限 4

设计约束：

1. **题型数据来源**：``course_kb.type``（单选/多选/填空/解答/实验/压轴），
   经 ``course_kb_embeddings.type`` 随检索 chunks 带回——迁移见
   ``scripts/add_qtype_to_embeddings.sql``（线上该列由本迁移补齐并回填）。
2. **难度口径统一**：``course_kb.difficulty`` 线上是**整数 1-5**（4/5 视为 hard），
   旧种子/CSV 里出现过 ``'hard'``/``'困难'``/``'基础'``/``'中档'`` 等字符串。
   ``difficulty_is_hard`` 做全形态归一化——T-C2 的 ``is_finale_question``
   曾只认 ``'hard'`` 字符串，在线上整数数据下**永不命中**（压轴封顶实际失效），
   此处顺带修正（socratic.is_finale_question 已改走本函数）。
3. **保守兜底**：无标注题（检索未命中 / chunks 无 type）按中等处理——
   不猜记忆型直接讲解，也不轻率压轴化（与 T-C2 "无标注按普通题"同口径）。

分步拆解复用 T1/T3 的 ``course_kb_steps``（211 题 668 步全覆盖）：
``step_guidance`` 从 judge_v2.load_bundle 的 bundle 里取当前步素材；
``step_progress_signal`` 判定"有实质回应/答对"以推进到下一步。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 题型分档

TIER_MEMORY = "memory"   # 记忆型：单选/多选 -> 直接讲解
TIER_MEDIUM = "medium"   # 中等：填空/解答（非压轴）-> 引导 1-2 轮
TIER_FINALE = "finale"   # 压轴：解答类 + ≥3 步 + hard -> 3-4 轮 + 分步拆解

TIER_NAMES = {
    TIER_MEMORY: "记忆型",
    TIER_MEDIUM: "中等",
    TIER_FINALE: "压轴",
}

#: 各档位的引导轮次上限（计划书：记忆型直接讲解 / 中等 1-2 轮 / 压轴 3-4 轮）
MAX_ROUNDS = {
    TIER_MEMORY: 1,
    TIER_MEDIUM: 2,
    TIER_FINALE: 4,
}

#: 压轴题分步拆解的最小步骤数（计划书口径：解答中带 ≥3 步）
STEP_GUIDED_MIN_STEPS = 3

#: 整数难度里视为 hard 的阈值（course_kb.difficulty 线上为 1-5）
HARD_DIFFICULTY_MIN = 4

# 记忆型题型：直接讲解，不进阶梯绕弯（judge_v2.OBJECTIVE_TYPES 同口径）
MEMORY_TYPES = ("单选", "多选", "选择")
# 过程类题型：填空/解答/实验/证明/压轴——按步骤推进（judge_v2 同口径）
PROCESS_TYPES = ("填空", "解答", "实验", "证明", "压轴")

# 难度为 hard 的字符串形态（旧种子/CSV 数据；线上整数形态走 HARD_DIFFICULTY_MIN）
_HARD_WORDS = ("hard", "困难", "难")
_EASY_WORDS = ("easy", "简单", "基础")
_MEDIUM_WORDS = ("medium", "中等", "中档")


def difficulty_is_hard(value) -> bool:
    """难度是否 hard——全形态归一化（纯函数）。

    覆盖形态：整数 4/5、字符串 ``'4'``/``'5'``、``'hard'``/``'困难'``。
    其余（``'基础'``/``'中档'``/``1``-``3``/空）一律 False——判不准就不压轴化。
    """
    if value is None:
        return False
    if isinstance(value, bool):  # bool 是 int 子类，先挡掉
        return False
    if isinstance(value, (int, float)):
        return value >= HARD_DIFFICULTY_MIN
    s = str(value).strip().lower()
    if not s:
        return False
    if s.isdigit():
        return int(s) >= HARD_DIFFICULTY_MIN
    if any(w in s for w in _HARD_WORDS):
        return True
    return False


def _first_chunk(context: dict | None) -> dict:
    for c in (context or {}).get("chunks", []):
        if isinstance(c, dict):
            return c
    return {}


def _qtype_of(context: dict | None, bundle: dict | None) -> str:
    """题型取值：bundle（course_kb.type，权威）优先，其次检索 chunk 的 type。"""
    if bundle and bundle.get("type"):
        return str(bundle["type"]).strip()
    return str(_first_chunk(context).get("type") or "").strip()


def _steps_count(bundle: dict | None) -> int:
    return len(bundle.get("steps") or []) if bundle else 0


def route_question_tier(context: dict | None, bundle: dict | None = None) -> dict:
    """题型路由（纯函数）：返回响应策略。

    返回 ``{"tier", "tier_name", "qtype", "max_rounds", "step_guided", "reason"}``：

    - 单选/多选 -> memory（max_rounds=1，题型优先于难度——再难的选择题也是记忆型）；
    - 填空 -> medium（max_rounds=2）；
    - 解答/实验/证明/压轴：步骤数 ≥3 且难度 hard -> finale（max_rounds=4，
      step_guided=True）；否则 medium；
    - 无题型信息（检索未命中/dev 种子）-> 保守 medium。

    ``bundle`` 为 ``judge_v2.load_bundle`` 的产物（含权威 type 与 steps）；
    不传时只用 chunks 信号——此时步骤数未知，压轴判定必然不成立（保守 medium）。
    """
    qtype = _qtype_of(context, bundle)
    steps = _steps_count(bundle)
    chunk = _first_chunk(context)
    hard = difficulty_is_hard(chunk.get("difficulty")) or difficulty_is_hard(
        (bundle or {}).get("difficulty")
    )

    if qtype and any(t in qtype for t in MEMORY_TYPES):
        return {
            "tier": TIER_MEMORY,
            "tier_name": TIER_NAMES[TIER_MEMORY],
            "qtype": qtype,
            "max_rounds": MAX_ROUNDS[TIER_MEMORY],
            "step_guided": False,
            "reason": f"{qtype}为记忆型，直接讲解",
        }

    if qtype and any(t in qtype for t in PROCESS_TYPES):
        if steps >= STEP_GUIDED_MIN_STEPS and hard:
            return {
                "tier": TIER_FINALE,
                "tier_name": TIER_NAMES[TIER_FINALE],
                "qtype": qtype,
                "max_rounds": MAX_ROUNDS[TIER_FINALE],
                "step_guided": True,
                "reason": f"{qtype}带 {steps} 步且难度 hard，压轴分步拆解",
            }
        return {
            "tier": TIER_MEDIUM,
            "tier_name": TIER_NAMES[TIER_MEDIUM],
            "qtype": qtype,
            "max_rounds": MAX_ROUNDS[TIER_MEDIUM],
            "step_guided": False,
            "reason": (
                f"{qtype}非压轴（步骤 {steps}，难度{'hard' if hard else 'non-hard'}）"
            ),
        }

    # 无题型信息：保守按中等——不猜记忆型直接讲解，也不轻率压轴化
    return {
        "tier": TIER_MEDIUM,
        "tier_name": TIER_NAMES[TIER_MEDIUM],
        "qtype": qtype,
        "max_rounds": MAX_ROUNDS[TIER_MEDIUM],
        "step_guided": False,
        "reason": "无题型标注（检索未命中），保守按中等处理",
    }


def step_guidance(bundle: dict, step_idx: int) -> dict | None:
    """取压轴题当前步骤的引导素材（纯函数）。

    ``bundle`` 需含 ``steps``（judge_v2.load_bundle 产物，按 step_no 有序）。
    返回 ``{"step_no","total","label","text","explanation","is_last"}``；
    步骤不足分步拆解门槛时返回 None（调用方回落普通阶梯）。
    """
    steps = bundle.get("steps") or []
    if len(steps) < STEP_GUIDED_MIN_STEPS:
        return None
    idx = min(max(int(step_idx), 0), len(steps) - 1)
    st = steps[idx]
    text = (st.get("text") or "").strip()
    label = (st.get("label") or "").strip()
    explanation = (st.get("explanation") or "").strip()
    if not text:
        text = f"第 {idx + 1} 步"
    return {
        "step_no": idx + 1,
        "total": len(steps),
        "label": label,
        "text": text,
        "explanation": explanation,
        "is_last": idx == len(steps) - 1,
        # 全步骤摘要（L4 完整解析/复盘用；压轴题 steps 已 ≥3，量可控）
        "all_steps": [
            (s.get("text") or "").strip() or f"第 {i + 1} 步"
            for i, s in enumerate(steps)
        ],
    }


def step_progress_signal(
    judgement: dict | None, student_reply: str | None, frustration_streak: int = 0
) -> bool:
    """压轴分步推进信号（纯函数）：本轮是否算"有实质进展"，推进到下一步。

    - 判分正确（judge_v2 correct=True）-> 推进（答对是该轮最大的推进信号）；
    - 有实质回应（非挫败表达）-> 推进（与 T-C2 "有实质回应即清零"同口径）；
    - 挫败表达 / 空回应 -> 不推进（停留在当前步，靠 Hint 升级给更多信息）。
    """
    if judgement and judgement.get("correct") is True:
        return True
    if frustration_streak > 0:
        return False
    return bool((student_reply or "").strip())
