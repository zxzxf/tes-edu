"""判分护栏（M2-10 / G3）：同一标答累计 K 次改判 → 冻结该题并告警。

为什么需要它
------------
``equiv`` 的准确性由 gold set（M2-8）守门，但那是**离线**门禁。线上真正的信号是
教师的**改判**：同一道题、同一份标答，被老师反复改判，说明这份标答（或它的
可接受答案值集合）本身有问题——机器按它判，会把一批本答对的学生判错，而且
**错得很稳定**（同一标答 → 同一结论），不会因为学生数量增加而自己暴露。

只靠 ``manual_pending`` 兜不住：只要机器敢判，就轮不到人工。所以必须有人
把「机器判错了」这件事记下来，攒够次数就把这道题的自动判分**关掉**。

规则
----
1. 每次**有效改判**给 ``(题目内容, 标答指纹)`` 计一次。「有效」有两个条件，缺一不可：
   - **推翻已有判定**（首次判定不算，见 ``counts_as_flip``）；
   - **判定真的变了**（重复点保存、补评语不算，见 ``is_effective_flip``）。
   少任何一个条件，护栏都会变成「三次点击就冻结」的误伤机器。
2. 累计到阈值 K（``GRADING_FREEZE_THRESHOLD``，默认 3）→ **冻结该题**：
   
   - 该题在**任何卷**里都不再自动判分（新提交一律转人工）；
   - **禁止再发布**含这道题的新卷（``publish`` 返回 409）——已知有争议的题
     不该继续发给学生，让学生按一份可疑答案自评；
   - 发一条告警，附「已按该标答自动判为错的提交数」= 影响面（不自动改历史成绩：
     静默改分比判错更难解释）。
3. 复核完（修答案 / 确认标答无误）→ 解冻，计数清零重新观察；再犯会再次冻结。

关于「冻结到底拦住了什么」（如实说明）
--------------------------------------
本系统的入口只有「机器判不了 → 老师判」，所以被反复改判的题，多半本来就是
人工题，冻结对**它的历史提交**没有额外影响。冻结真正的价值在另外三处：

* 该题后续**任何卷**都不再自动判（题目被修好之前，别让它再批量产出机器结论）；
* **发布闸门**：有争议的题不许再发给学生（这才是能立刻止损的一环）；
* 告警强制人工处置：标答错了会直接印在学生的成绩页上，比判错更伤。

等「已判题复核/抽检」入口（M2-7 指标、M2-12 错题回流之后）上线，同一套计数规则
会自动覆盖自动判分的题，无需改动。

为什么键是 ``(content_hash, 标答指纹)`` 而不是 ``paper_question_id``
--------------------------------------------------------------------
``exam_paper_questions`` 是**随卷快照**：同一道题在每张卷里是一行独立记录。
按 paper_question_id 冻结只能护住出问题的那一张卷，重新组卷又是一行新记录，
照样按错答案判——冻结等于没冻。按 ``content_hash``（题面身份）才能跨卷生效。

再叠加**标答指纹**，解决「答案改好了还继续被冻着」：标答一改指纹就变，新指纹
从 0 开始观察；旧指纹下的冻结记录只保护仍在用旧快照的老卷。这两点合起来，
才是「同一答案累计 K 次改判」的字面含义——**换了答案就不是同一件事了**。

本模块是纯函数（不碰 DB / ORM），便于单测；落库与流转见
``app/services/grading_guard.py``。
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from app.core.equiv import norm_text

# 冻结阈值默认值。线上可用环境变量 GRADING_FREEZE_THRESHOLD 覆盖。
DEFAULT_THRESHOLD = 3

# 冻结状态机：watching（观察中，计数未到）→ frozen（已冻结）→ released（已解冻）
STATUS_WATCHING = "watching"
STATUS_FROZEN = "frozen"
STATUS_RELEASED = "released"

# 告警事件类型（append-only 流水，审计与指标用）
ALERT_FREEZE = "freeze"          # 首次冻结
ALERT_REFREEZE = "refreeze"      # 解冻后又攒够次数，再次冻结
ALERT_RELEASE = "release"        # 教师解冻


def answer_fingerprint(answer: str) -> str:
    """标答指纹（16 位十六进制）。

    用同一套归一化（``equiv.norm_text``）再取摘要：``\\dfrac32`` 与 ``3/2``、
    全角与半角、大小写差异都应落在同一指纹上——它们本来就是同一份答案。
    """
    norm = norm_text(answer or "")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def verdict_of(correct: Optional[bool], partial: Optional[bool]) -> str:
    """(correct, partial) → ``correct`` / ``partial`` / ``wrong``。

    与 ``grading._verdict_flags`` 同一口径，方向相反。``partial`` 优先于 ``wrong``
    但低于 ``correct``：漏选不算对，但也不是「完全错」。
    """
    if correct:
        return "correct"
    if partial:
        return "partial"
    return "wrong"


def is_effective_flip(
    before: Tuple[Optional[bool], Optional[bool]],
    after: Tuple[Optional[bool], Optional[bool]],
) -> bool:
    """这次提交是否**真的改了判定**。

    只有判定变化才算「改判」。教师把同一份工单重复提交（网络重试、点两次保存、
    补一句评语）不改变结论，不应消耗冻结额度——否则阈值形同虚设。
    """
    return verdict_of(*before) != verdict_of(*after)


def counts_as_flip(
    was_reviewed: bool,
    before: Tuple[Optional[bool], Optional[bool]],
    after: Tuple[Optional[bool], Optional[bool]],
) -> bool:
    """这次提交是否计入冻结额度。

    **必须是「推翻已有判定」才算改判**，这是本模块最容易写错、后果最重的一条：

    - 首次判定（``manual_pending`` → 任何结论）**不计**。学生提交后被转人工，
      本来就要老师从无到有判一次；把「第一次判」算成「改判」，任何一道填空题
      上来三个学生就自动冻结，护栏立刻变成全站停摆。
    - 已有结论被推翻（correct → wrong / wrong → partial …）才计。这才是
      「这个判定本身不可靠」的证据。
    - 推翻两次再翻回来（A→B→A）算 2 次：每次改变都是一次争议。
    """
    if not was_reviewed:
        return False
    return is_effective_flip(before, after)


def threshold() -> int:
    """当前生效的冻结阈值（每次读，便于测试与热改配置）。"""
    try:
        from app.core.config import settings

        val = int(getattr(settings, "grading_freeze_threshold", DEFAULT_THRESHOLD) or 0)
    except Exception:  # pragma: no cover - 配置损坏时退回默认值，不阻断判分
        return DEFAULT_THRESHOLD
    return val if val > 0 else DEFAULT_THRESHOLD


def student_frozen_reason() -> str:
    """学生端看到的原因。**不能**写「老师判错了」这种自我否定的话术。"""
    return "这道题的标答正在复核，老师会人工判定（不计错）"


def freeze_message(flips: int, threshold_value: int, refreeze: bool = False) -> str:
    """告警正文：说清「第几次、为什么、影响面自己去哪看」。"""
    prefix = "解冻后再次出现" if refreeze else ""
    return (
        f"{prefix}同一标答已累计 {flips} 次改判（阈值 {threshold_value}）："
        f"这道题的标答很可能有误，已冻结自动判分并转人工复核"
    )


def impact_message(affected: int) -> str:
    """影响面说明。0 与 >0 的语气必须不同，否则老师会当成例行提示滑过去。"""
    if affected <= 0:
        return "暂无已被自动判错的提交（冻结及时）"
    return f"已有 {affected} 份提交按该标答自动判为错，建议复核后决定是否重新判定"


def watching_message(flips: int, threshold_value: int) -> str:
    """观察态提示（教师打开工单时才看，不对外告警）。"""
    return (
        f"这道题的标答已累计 {flips}/{threshold_value} 次改判，"
        f"达到阈值将冻结自动判分"
    )
