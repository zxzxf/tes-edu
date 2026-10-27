"""P2 引导式对话 —— Hint 阶梯 L0–L4（T-C2）。

阶梯定义（与分期书/计划书口径一致）：

- ``L0`` 点考点      —— 只告诉学生"这道题考什么"，邀请他说出第一个困惑点
- ``L1`` 给方向      —— 给出该类题的通法方向（不给公式细节）
- ``L2`` 给关键公式  —— 给出关键公式/依据，让学生自己代入
- ``L3`` 给该步推导  —— 给出当前这一步的推导过程，**仍不给最终答案**
- ``L4`` 完整解析    —— 完整解析 + 复盘三问（这题考什么/卡在哪一步/同型题怎么识别）

升级策略：

- 默认从 L0 起，每轮 Hint 由 ``decide_level`` 结合对话状态推进；
- 学生连续 ``FRUSTRATION_JUMP``（2）次挫败信号（"我不懂/直接说吧"）跳一级；
- 压轴题（检索上下文 difficulty=hard）封顶 L3 循环分步，L4 只能由学生主动
  点"直接看解析"（``direct_reveal``）进入——防"挫败两次就白拿完整解析"；
- 学生主动 ``direct_reveal`` 直达 L4（T-C3 将其做成一等功能 + 埋点）。

"禁止空反问"落地为契约函数 ``hint_contract_violations``：每一级 Hint 必须
① 不是纯问句 ② 含明确的下一步动作指引；L1 额外必须含方向性表述；L4 额外
必须含复盘三问。LLM 生成的 Hint 先过契约，违约或调用失败都回落
``template_hint`` 模板——**契约不依赖 LLM 也能保证基本教学质量**。

阶梯状态按 (学生, 题) 维护于进程内字典 ``HintLadder``（单副本 agent-backend
冒烟足够；PG 持久化按计划书"冒烟后固化"留待 T-C4 埋点需求明确时再做）。

T-C3 起：题型路由（qtype.route_question_tier）决定各题的轮次上限与是否走
``course_kb_steps`` 分步拆解；``template_hint`` / ``generate_hint`` 接受可选
``step`` 参数（qtype.step_guidance 产物）把当前步骤素材织入各级 Hint；
``HintLadder`` 状态增加 ``step`` 字段记录分步进度。
"""
from __future__ import annotations

import time

from app.core import config, llm_cache, model_router, qtype, tenant_llm, usage

# ---------------------------------------------------------------- 阶梯定义

LEVEL_L0 = 0  # 点考点
LEVEL_L1 = 1  # 给方向
LEVEL_L2 = 2  # 给关键公式
LEVEL_L3 = 3  # 给该步推导（不给最终答案）
LEVEL_L4 = 4  # 完整解析 + 复盘三问

LEVEL_NAMES = {
    LEVEL_L0: "点考点",
    LEVEL_L1: "给方向",
    LEVEL_L2: "给关键公式",
    LEVEL_L3: "给该步推导",
    LEVEL_L4: "完整解析+复盘",
}

MIN_LEVEL = LEVEL_L0
MAX_LEVEL = LEVEL_L4

#: 连续挫败信号达到多少次跳一级（计划书：连续 2 次"我不懂/直接说吧"）
FRUSTRATION_JUMP = 2

#: 压轴题的 Hint 封顶等级（L4 完整解析只能由学生主动 direct_reveal 进入）
FINALE_CAP = LEVEL_L3

# 挫败信号：学生在对话轮中表达"跟不上/算了/你直接讲吧"。
# 注意与 intent.py 的直给关键词分层不同——这里是对话轮内的挫败表达，
# 触发的是"跳一级"而非"直给"。
_FRUSTRATION_KEYWORDS = (
    "我不懂",
    "没懂",
    "还是不懂",
    "依然不懂",
    "完全不懂",
    "不太懂",
    "直接说吧",
    "直接讲吧",
    "你直接说",
    "你直接讲",
    "告诉我吧",
    "你告诉我",
    "算了",
    "放弃",
    "太难",
    "不会呀",
    "真不会",
    "还是不会",
    "跟不上了",
)

# "禁止空反问"契约关键词表
# ① 下一步动作指引：每一级都必须含其一
_ACTION_MARKERS = (
    "下一步",
    "你先",
    "先写出",
    "先说说",
    "先试着",
    "先看",
    "先说",
    "请先",
    "试试",
    "你来",
    "该你了",
)
# ② L1 方向性表述：必须含其一
_DIRECTION_MARKERS = (
    "方向",
    "通法",
    "思路",
    "关键",
    "方法",
    "入手",
    "着手",
    "先从",
    "从何",
)
# ③ L4 复盘三问：三个都必须出现
_REVIEW_MARKERS = ("这题考什么", "卡在哪", "同型题")


# ---------------------------------------------------------------- 纯函数层


def normalize_question(question: str) -> str:
    """阶梯状态键用的题目归一化：去首尾空白、压缩连续空白。"""
    import re

    return re.sub(r"\s+", " ", (question or "").strip())


def is_frustration_signal(reply: str | None) -> bool:
    """对话轮内的挫败信号检测（纯函数）。"""
    if not reply:
        return False
    return any(k in reply for k in _FRUSTRATION_KEYWORDS)


def is_finale_question(context: dict | None) -> bool:
    """压轴题判定：检索上下文任一命中题难度为 hard。

    数据来自 ``rag.retrieve`` 的 chunk 字段（course_kb.difficulty）。
    难度口径走 ``qtype.difficulty_is_hard`` 全形态归一化——线上 difficulty 是
    整数 1-5（4/5=hard），旧代码只认 ``'hard'`` 字符串会**永不命中**；
    无标注题（检索未命中）时保守返回 False——按普通题处理。
    """
    for c in (context or {}).get("chunks", []):
        if qtype.difficulty_is_hard(c.get("difficulty")):
            return True
    return False


def decide_level(
    current: int,
    frustration_streak: int = 0,
    *,
    direct_reveal: bool = False,
    finale: bool = False,
) -> int:
    """由当前等级 + 对话状态决定下一轮 Hint 等级（纯函数）。

    - ``direct_reveal``：学生主动点"直接看解析" -> 直达 L4（无视一切封顶）；
    - 连续挫败达到 ``FRUSTRATION_JUMP`` -> 跳一级；
    - 压轴题封顶 L3（循环分步），L4 只能由 direct_reveal 进入；
    - 其余情况等级不变。
    """
    if direct_reveal:
        return LEVEL_L4
    cap = FINALE_CAP if finale else LEVEL_L4
    level = min(max(int(current), MIN_LEVEL), cap)
    if frustration_streak >= FRUSTRATION_JUMP:
        level = min(level + 1, cap)
    return level


def _is_question_only(text: str) -> bool:
    """整条是否只是一个问句（没有任何陈述性内容）——即"空反问"。"""
    t = text.strip()
    if not t.endswith(("？", "?")):
        return False
    # 有陈述句（句号/感叹号/换手分隔）-> 含信息，不算纯问句
    if any(sep in t for sep in "。！!\n"):
        return False
    return True


def hint_contract_violations(text: str | None, level: int) -> list[str]:
    """"禁止空反问"契约检查（纯函数）。返回违约项列表，空列表 = 通过。

    每一级必须：
      ① 非空；
      ② 不是纯问句（空反问）；
      ③ 含下一步动作指引；
    L1 额外必须含方向性表述；L4 额外必须含复盘三问。
    """
    t = (text or "").strip()
    if not t:
        return ["empty"]
    violations: list[str] = []
    if _is_question_only(t):
        violations.append("question_only")
    if not any(m in t for m in _ACTION_MARKERS):
        violations.append("missing_action")
    if level == LEVEL_L1 and not any(m in t for m in _DIRECTION_MARKERS):
        violations.append("missing_direction")
    if level == LEVEL_L4 and not all(m in t for m in _REVIEW_MARKERS):
        violations.append("missing_review")
    return violations


def _points_text(context: dict | None) -> str:
    points = list(
        dict.fromkeys(
            c.get("knowledge_point")
            for c in (context or {}).get("chunks", [])
            if c.get("knowledge_point")
        )
    )
    return "、".join(points) if points else "本题涉及的核心知识（检索未命中，按通法处理）"


def _evidence(context: dict | None) -> str:
    """检索到的第一条例题片段，作为通法/公式/推导的素材来源。"""
    for c in (context or {}).get("chunks", []):
        text = (c.get("text") or "").strip()
        if text:
            return text[:120]
    return "（检索未命中，按该类题通用方法处理）"


def _steps_digest(step: dict) -> str:
    """分步素材的全步骤摘要（L4 完整解析用）：①②③… 顺排。"""
    all_steps = step.get("all_steps") or []
    if not all_steps:
        return step.get("text", "")
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    return " ".join(
        f"{marks[i] if i < len(marks) else f'({i + 1})'}{t[:40]}"
        for i, t in enumerate(all_steps)
    )


def template_hint(
    question: str, context: dict | None, level: int, step: dict | None = None
) -> dict:
    """五级 Hint 模板（纯函数，全部通过 hint_contract_violations 契约）。

    LLM 不可用/输出违约时的回落——保证契约不依赖 LLM 也能成立。

    T-C3：``step`` 为 ``qtype.step_guidance`` 产物（压轴题分步拆解的当前步），
    传入时各级模板织入"第 N/M 步 + 步骤素材"，一步一 Hint；
    不传（中等题/无步骤数据）走原有通用模板。
    """
    points = _points_text(context)
    evidence = _evidence(context)

    if step:
        # 压轴题分步模板：每级都锚定当前步骤（step_no/total/label/text）
        sn, total = step["step_no"], step["total"]
        label = f"（{step['label']}）" if step.get("label") else ""
        stext = step["text"]
        sexp = step.get("explanation") or stext

        if level == LEVEL_L0:
            return {
                "answer": (
                    f"这道题考的是：{points}。\n"
                    f"我们分 {total} 步来走，先看第 {sn}/{total} 步{label}：{stext}\n"
                    "下一步：你先说说，这一步打算从哪里下手？"
                ),
                "steps": [
                    f"考点定位：{points}",
                    f"第 {sn}/{total} 步{label}：{stext[:40]}",
                    "说出这一步的下手思路（不用算完）",
                ],
            }

        if level == LEVEL_L1:
            return {
                "answer": (
                    f"这类题的通法方向：从「{points}」入手，"
                    f"第 {sn}/{total} 步的方向是先把条件理清楚——{stext}\n"
                    "下一步：你按这个方向把这一步的算式雏形列出来（不用解完）。"
                ),
                "steps": [
                    f"方向：{points} 的通用解法",
                    f"第 {sn}/{total} 步{label}：{stext[:40]}",
                    "列出这一步的算式雏形（不求结果）",
                ],
            }

        if level == LEVEL_L2:
            return {
                "answer": (
                    f"关键依据（第 {sn}/{total} 步{label}）：{sexp}\n"
                    "下一步：你把题目里的数值代进这个依据，先写出代入后的式子"
                    "（这一步不用算出结果）。"
                ),
                "steps": [
                    f"关键依据：{sexp[:40]}",
                    f"第 {sn}/{total} 步代入题目数值",
                    "写出代入后的式子（不求结果）",
                ],
            }

        if level == LEVEL_L3:
            return {
                "answer": (
                    f"这一步这样推（第 {sn}/{total} 步{label}）：{sexp}\n"
                    "（到这一步为止，最终答案先不给你——那一步才是本题的 key。）\n"
                    "下一步：你按这个推导把这一步写完，写完好发我看对错。"
                ),
                "steps": [
                    f"该步推导：{sexp[:40]}",
                    f"照推导写完第 {sn}/{total} 步",
                    "提交这一步，进入下一步",
                ],
            }

        # LEVEL_L4：完整解析（含全部步骤）+ 复盘三问
        return {
            "answer": (
                f"完整解析如下。考点：{points}。同类题思路：{evidence}\n"
                f"本题共 {total} 步，完整步骤：{_steps_digest(step)}\n"
                "下一步（复盘三问）：\n"
                "① 这题考什么：能否一句话说出考点与通法？\n"
                "② 卡在哪一步：回看刚才哪一级 Hint 让你「卡住」，是条件没用上还是公式记错？\n"
                "③ 同型题怎么识别：下次看到什么特征（条件结构/问法），就该直接想到这套通法？"
            ),
            "steps": [
                f"完整解析：考点 {points}，思路参照 {evidence[:40]}",
                f"分步还原：共 {total} 步，从第 1 步顺推到第 {total} 步",
                "复盘①：一句话说出考点与通法",
                "复盘②：定位刚才卡住的那一级",
                "复盘③：总结同型题的识别特征",
            ],
        }

    if level == LEVEL_L0:
        # 点考点：只告诉学生考什么，邀请说出困惑点（不给任何解法）
        return {
            "answer": (
                f"这道题考的是：{points}。\n"
                "先别急着算——我们先把题目里的已知条件和所求量拆开看。\n"
                "下一步：你先说说，这些条件里哪一个一眼看不出用途？"
            ),
            "steps": [
                f"考点定位：{points}",
                "拆解已知条件与所求量",
                "说出你最没把握的那个条件（不用算）",
            ],
        }

    if level == LEVEL_L1:
        # 给方向：通法方向（不给公式细节）
        return {
            "answer": (
                f"这类题的通法方向：从「{points}」入手，"
                f"参照同类题的路子——{evidence}\n"
                "下一步：你先把已知条件按这个方向列出来，写成算式雏形（不用解完）。"
            ),
            "steps": [
                f"方向：{points} 的通用解法",
                f"参照同类题：{evidence[:40]}",
                "列出已知条件的算式雏形",
            ],
        }

    if level == LEVEL_L2:
        # 给关键公式：给出依据，让学生自己代入
        return {
            "answer": (
                f"关键依据：{evidence}\n"
                "下一步：你把题目里的数值代进这个依据，先写出代入后的式子"
                "（这一步不用算出结果）。"
            ),
            "steps": [
                f"关键依据：{evidence[:40]}",
                "代入题目数值",
                "写出代入后的式子（不求结果）",
            ],
        }

    if level == LEVEL_L3:
        # 给该步推导：给当前这一步的过程，仍不给最终答案
        return {
            "answer": (
                f"这一步这样推：{evidence}\n"
                "（到这一步为止，最终答案先不给你——那一步才是本题的 key。）\n"
                "下一步：你按这个推导把这一步写完，写完好发我看对错。"
            ),
            "steps": [
                f"该步推导：{evidence[:40]}",
                "照推导写完这一步",
                "提交这一步，进入下一步",
            ],
        }

    # LEVEL_L4：完整解析 + 复盘三问（direct_reveal 或非压轴题挫败到顶）
    return {
        "answer": (
            f"完整解析如下。考点：{points}。同类题思路：{evidence}\n"
            "下一步（复盘三问）：\n"
            "① 这题考什么：能否一句话说出考点与通法？\n"
            "② 卡在哪一步：回看刚才哪一级 Hint 让你「卡住」，是条件没用上还是公式记错？\n"
            "③ 同型题怎么识别：下次看到什么特征（条件结构/问法），就该直接想到这套通法？"
        ),
        "steps": [
            f"完整解析：考点 {points}，思路参照 {evidence[:40]}",
            "复盘①：一句话说出考点与通法",
            "复盘②：定位刚才卡住的那一级",
            "复盘③：总结同型题的识别特征",
        ],
    }


# ---------------------------------------------------------------- 状态层


class HintLadder:
    """(学生, 题) 维度的阶梯状态：进程内字典。

    键 = ``student_id|normalized_question``；
    值 = ``{"level", "streak", "rounds", "step"}``（T-C3 起 ``step`` 记录
    压轴题分步拆解进度，0 起，非压轴题恒 0）。
    容量上限 ``max_entries``，超出淘汰最早插入的条目（学习会话天然有局部性，
    LRU 精确化留作 T-F 数据治理项）。
    """

    def __init__(self, max_entries: int = 2048) -> None:
        self._states: dict[str, dict] = {}
        self._max_entries = max_entries

    @staticmethod
    def key(student_id: str | None, question: str) -> str:
        return f"{student_id or ''}|{normalize_question(question)}"

    def get(self, student_id: str | None, question: str) -> dict:
        """取阶梯状态；无历史返回 L0 初始态（默认从 L0 起）。"""
        return dict(
            self._states.get(
                self.key(student_id, question),
                {"level": LEVEL_L0, "streak": 0, "rounds": 0, "step": 0},
            )
        )

    def record_round(
        self,
        student_id: str | None,
        question: str,
        *,
        level: int,
        streak: int,
        step: int | None = None,
    ) -> None:
        """落库本轮结束后的阶梯状态。

        ``step`` 为 None（非压轴题/无分步数据）时保留原 step 值不覆盖。
        """
        k = self.key(student_id, question)
        prev = self._states.get(k, {"rounds": 0, "step": 0})
        self._states[k] = {
            "level": level,
            "streak": streak,
            "rounds": int(prev.get("rounds", 0)) + 1,
            "step": int(prev.get("step", 0)) if step is None else int(step),
        }
        if len(self._states) > self._max_entries:
            # dict 保序：淘汰最早插入的一条
            oldest = next(iter(self._states))
            if oldest != k:
                del self._states[oldest]


#: 模块级单例（agent-backend 单副本；PG 持久化待 T-C4 冒烟后固化）
ladder = HintLadder()


# ---------------------------------------------------------------- LLM 生成层

_HINT_SYSTEM = (
    "你是一名高中数学导师，正在用苏格拉底式 Hint 阶梯引导学生解题。\n"
    "阶梯等级：L0 点考点 / L1 给方向 / L2 给关键公式 / "
    "L3 给该步推导（不给最终答案）/ L4 完整解析+复盘三问。\n"
    "硬性规则：\n"
    "1) 必须依据【检索到的知识点与例题】作答，不得编造；\n"
    "2) 除 L4 外严禁给出最终答案；\n"
    "3) 每一级必须包含：指向考点的可操作信息 + 明确的下一步动作指引；\n"
    "4) 禁止空反问（只提问、不给任何信息）；\n"
    "5) L1 必须含方向性表述（通法/思路/方向），L4 必须在完整解析后附复盘三问"
    "（这题考什么 / 卡在哪一步 / 同型题怎么识别）。\n"
    "输出严格 JSON 且不要包裹 markdown：{\"answer\": \"...\", \"steps\": [\"...\"]}。"
)


def _build_hint_messages(
    question: str,
    context: dict | None,
    level: int,
    history: list[str] | None,
    step: dict | None = None,
) -> list[dict]:
    chunks = (context or {}).get("chunks", [])
    if chunks:
        ctx_text = "\n\n".join(
            f"[例题{i + 1}] {c.get('text', '')}\n知识点：{c.get('knowledge_point', '')}"
            for i, c in enumerate(chunks)
        )
    else:
        ctx_text = "（无检索结果，请按该类题通用方法处理并说明）"
    user = (
        f"【检索到的知识点与例题】\n{ctx_text}\n\n"
        f"【学生问题】\n{question}\n\n"
        f"【本轮 Hint 等级】L{level}（{LEVEL_NAMES.get(level, '')}）"
    )
    if step:
        # T-C3 压轴题分步拆解：把当前步骤素材喂给 LLM，一步一 Hint
        user += (
            f"\n\n【分步拆解】本题共 {step['total']} 步，当前第 {step['step_no']} 步"
            + (f"（{step['label']}）" if step.get("label") else "")
            + f"：{step['text']}\n"
            f"该步要点：{step.get('explanation') or step['text']}\n"
            "本轮 Hint 必须锚定当前步骤展开，不得跳到其他步骤。"
        )
    if history:
        turns = "\n".join(f"- {h}" for h in history[-6:])
        user += f"\n\n【对话历史（近轮）】\n{turns}"
    return [
        {"role": "system", "content": _HINT_SYSTEM},
        {"role": "user", "content": user},
    ]


async def _remote_hint(
    question: str,
    context: dict | None,
    level: int,
    history: list[str] | None = None,
    step: dict | None = None,
    *,
    subject: str | None = None,
    content_hash: str | None = None,
    tier: str | None = None,
    store=None,
    llm_cfg=None,
) -> dict | None:
    """LLM 生成 Hint（T-D1 缓存：同题同 Hint 级命中则跳过 LLM）。

    返回 None 表示失败（无密钥/超时/解析失败）——此类结果不缓存。
    契约校验不在这里做，统一收口到 ``generate_hint``。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。
    """
    if not config.llm_ready():
        return None

    cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)

    async def _call(q, ctx, m):
        return await _remote_hint_raw(
            q, ctx, level, history, step, tier=tier, subject=subject, llm_cfg=cfg
        )

    def _on_cache_hit() -> None:
        # T-D3：Hint 是消耗大头，命中也要计数（缓存省了多少）
        usage.record_usage_bg(
            mode="guide", subject=subject, cache_hit=True, grade="cache",
            model=cfg.chat_model,
        )

    # 上下文变体：对话历史与当前步骤会实质改变 Hint 输出，必须入键（否则串味）；
    # 非空时只做精确命中，不走近似复用。
    variant = ""
    if history or step:
        import json as _json

        variant = _json.dumps(
            {"history": history or [], "step": step or {}},
            ensure_ascii=False,
            sort_keys=True,
        )

    return await llm_cache.cached_solve(
        _call,
        question,
        context,
        "guide",
        subject=subject,
        hint_level=level,
        content_hash=content_hash,
        variant=variant or None,
        store=store,
        on_hit=_on_cache_hit,
        embed_cfg=cfg,
    )


async def _remote_hint_raw(
    question: str,
    context: dict | None,
    level: int,
    history: list[str] | None = None,
    step: dict | None = None,
    *,
    tier: str | None = None,
    subject: str | None = None,
    llm_cfg=None,
) -> dict | None:
    """原始 LLM Hint 生成（无缓存包装）。失败返回 None（T-D3：并记一行降级用量）。"""
    t0 = time.monotonic()
    try:
        cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)
        # T-D2：Hint 属引导档，默认小模型；压轴（tier=finale）上大模型
        route = model_router.route_model(
            mode="guide", context=context, tier=tier,
            simple=cfg.model_simple, hard=cfg.model_hard,
        )
        client = tenant_llm.make_client(cfg)
        resp = await client.chat.completions.create(
            model=route["model"],
            messages=_build_hint_messages(question, context, level, history, step),
            temperature=0.3,
        )
        content = resp.choices[0].message.content or ""
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").replace("json", "", 1).strip()
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        answer = data.get("answer") or content
        steps = data.get("steps") or []
        if isinstance(steps, str):
            steps = [steps]
        usage.record_usage_bg(
            model=route["model"], grade=route["grade"], mode="guide", subject=subject,
            latency_ms=int((time.monotonic() - t0) * 1000),
            prompt_tokens=_tk(resp, "prompt_tokens"),
            completion_tokens=_tk(resp, "completion_tokens"),
        )
        return {"answer": str(answer), "steps": [str(s) for s in steps]}
    except Exception as e:  # noqa: BLE001 生成失败不阻塞引导链路，回落模板
        usage.record_usage_bg(
            mode="guide", subject=subject, degraded=True,
            error_kind=usage.classify_error(e),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return None


def _tk(resp, key: str) -> int:
    """取响应的 token 计数（端点可能不返回 usage）。"""
    try:
        u = getattr(resp, "usage", None)
        return int(getattr(u, key, 0) or 0) if u is not None else 0
    except Exception:  # noqa: BLE001
        return 0


async def generate_hint(
    question: str,
    context: dict | None,
    level: int,
    history: list[str] | None = None,
    step: dict | None = None,
    *,
    subject: str | None = None,
    content_hash: str | None = None,
    tier: str | None = None,
    store=None,
    llm_cfg=None,
) -> dict:
    """生成本轮 Hint：LLM 优先，失败/违约回落 template_hint。

    契约闸门（唯一收口点）：LLM 输出先过 ``hint_contract_violations``，
    违约（空反问/缺动作/L1 缺方向/L4 缺复盘）一律回落模板——
    **契约不依赖 LLM 的自觉性**。

    T-C3：``step``（qtype.step_guidance 产物）传入时模板与 LLM prompt
    都锚定压轴题当前步骤。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。

    返回值形如 ``{"answer": ..., "steps": [...]}``。
    """
    try:
        hint = await _remote_hint(
            question, context, level, history, step,
            subject=subject, content_hash=content_hash, tier=tier, store=store,
            llm_cfg=llm_cfg,
        )
    except Exception:  # noqa: BLE001 双保险：生成层任何意外都回落模板
        hint = None
    violated = hint is not None and bool(hint_contract_violations(hint.get("answer"), level))
    if hint is None or violated:
        reason = "contract_violation" if violated else "llm_unavailable"
        hint = template_hint(question, context, level, step)
        # T-D3：降级必须显式标记——否则"LLM 挂了"在埋点里是隐形的。
        # dev（未配 LLM）本就是模板模式，不算降级。
        if config.llm_ready():
            hint["degraded"] = True
            hint["degraded_reason"] = reason
    return hint
