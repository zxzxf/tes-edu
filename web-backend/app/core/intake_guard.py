"""T-CAP4 入库自动校验闸门（纯函数，无 DB / 网络依赖）。

供两处复用：
* ``scripts/intake_guard.py``（独立运维脚本，对存量题补跑）
* ``app/crawl_service/intake.py``（采集链路 hook，新题入库即校验）

三闸与裁定口径详见 scripts/intake_guard.py 模块 docstring，此处只放可测纯函数。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Set

from app.core import equiv as eq
from app.core import answer_verify as av
from app.core.exam_types import to_qtype

VERDICT_PASS = "pass"
VERDICT_MANUAL = "manual_review"
VERDICT_QUARANTINE = "quarantine"

TAG_QUARANTINE = "guard_quarantine"
TAG_MANUAL = "guard_manual_review"
# 人工处置后的后缀（异常队列页用）：release/publish 落 released（可追溯「曾被隔离」），
# archive 落 archived（确认重复或脏题，保留数据但移出待办队列，不做硬删除）。
# 两者都保留原来源前缀，故回退/来源筛选谓词继续有效。
TAG_RELEASED = "guard_released"
TAG_ARCHIVED = "guard_archived"

# 采集链路的后缀标签：保留 auto_crawl 前缀，兼容回退/隔离谓词
CRAWL_TAG_BASE = "auto_crawl"
CRAWL_TAG_QUARANTINE = f"{CRAWL_TAG_BASE}:{TAG_QUARANTINE}"
CRAWL_TAG_MANUAL = f"{CRAWL_TAG_BASE}:{TAG_MANUAL}"


# ---------------------------------------------------------------- 闸1：去重
def text_fingerprint(stem: Any) -> Optional[str]:
    """题干文本指纹：norm_text 归一后剥掉所有标点/空白，只留字母数字。

    与 content_hash 互补：content_hash=sha256(stem|type|answer) 对标点/全角差异敏感
    （T-CAP1 实证 sid=3/61 同题异 hash）。本指纹抹平这些格式差异，专门抓
    「题干实质相同、指纹不同」的重复题。数字保留——数字变了就是不同题（变式），不判重。
    """
    s = eq.norm_text(str(stem or ""))
    s = "".join(ch for ch in s if ch.isalnum())
    return hashlib.sha1(s.encode("utf-8")).hexdigest() if s else None


def dedup_check(
    row: Dict[str, Any], *, known_hashes: Set[str], known_fps: Set[str],
) -> Optional[Dict[str, str]]:
    """命中重复返回带 verdict 的 dict，否则 None。known_* 不含题自身。"""
    ch = str(row.get("content_hash") or "").strip()
    if ch and ch in known_hashes:
        return {"verdict": VERDICT_QUARANTINE, "gate": "dedup_exact",
                "reason": f"content_hash 精确重复：{ch[:12]}"}
    fp = text_fingerprint(row.get("stem"))
    if fp and fp in known_fps:
        return {"verdict": VERDICT_QUARANTINE, "gate": "dedup_text",
                "reason": f"题干文本指纹重复（OCR/标点差异）：{fp[:12]}"}
    return None


# ---------------------------------------------------------------- 闸2：自洽
def _internal_qtype(raw: Any) -> str:
    """中文题型（填空/选择/解答）→ 内部题型（blank/choice/subjective）。"""
    parsed = to_qtype(str(raw or "").strip())
    if parsed:
        return parsed[0]
    return str(raw or "").strip().lower()


def _option_labels(options: Any) -> Set[str]:
    """从选项列表/dict 提取选项标签集合（选择题答案越界检查用）。"""
    labels: Set[str] = set()
    if isinstance(options, dict):
        for k in options:
            labels.add(str(k).strip().upper())
    elif isinstance(options, (list, tuple)):
        for opt in options:
            m = re.match(r"^\s*([A-Ha-h])\s*[\.、\):：,，]", str(opt))
            if m:
                labels.add(m.group(1).upper())
    return labels


def consistency_check(row: Dict[str, Any]) -> Dict[str, Any]:
    """答案自洽闸：pass / quarantine（明确错误）/ manual_review（无法自动判定）。"""
    answer = str(row.get("answer") or "").strip()
    if not answer:
        return {"verdict": VERDICT_QUARANTINE, "gate": "missing_answer",
                "reason": "答案为空，无法进入判分/组卷"}

    qtype = _internal_qtype(row.get("type"))
    options = row.get("options")
    labels = _option_labels(options)

    if labels and re.fullmatch(r"[A-Ha-h]+", answer):
        bad = [c for c in answer.upper() if c not in labels]
        if bad:
            return {"verdict": VERDICT_QUARANTINE, "gate": "answer_out_of_range",
                    "reason": f"答案 {answer.upper()} 含越界选项 {''.join(bad)}，实际选项 {sorted(labels)}"}
        return {"verdict": VERDICT_MANUAL, "gate": "choice_no_second_source",
                "reason": "选择题答案格式合法，但无第二来源/非数值，无法自动判对错"}

    mech = av.mechanical_check(
        qtype, answer, options=options, stem=str(row.get("stem") or ""),
    )
    if mech.get("applicable") and mech.get("passed") is True:
        return {"verdict": VERDICT_PASS, "gate": "mechanical_pass",
                "reason": mech.get("detail") or "机械代入通过"}
    if mech.get("applicable") and mech.get("passed") is False:
        return {"verdict": VERDICT_MANUAL, "gate": "mechanical_suspect",
                "reason": (mech.get("detail") or "代入未通过") + "（可能为含参题，不硬隔离）"}

    if eq.to_number(answer) is not None or eq.normalize_interval(answer) is not None:
        return {"verdict": VERDICT_MANUAL, "gate": "numeric_no_reference",
                "reason": "数值答案但题干无可代入等式，缺少自洽参照"}
    return {"verdict": VERDICT_MANUAL, "gate": "non_numeric",
            "reason": "非数值/解答题，equiv 无法确定性判定，转人工复核"}


# ---------------------------------------------------------------- 闸3：双源
def dual_source_check(
    row: Dict[str, Any], *, secondary_answer: Optional[str] = None,
    secondary_source: str = "kb_snapshot",
) -> Dict[str, Any]:
    """双源闸：仅当传入真正独立第二来源才比对；无来源 → manual（不误判）。"""
    if secondary_answer is None or not str(secondary_answer).strip():
        return {"verdict": VERDICT_MANUAL, "gate": "no_second_source",
                "reason": "无独立第二来源，G1 无法形成验证"}
    qtype = _internal_qtype(row.get("type"))
    ok = av.answer_equivalent(qtype, str(row.get("answer") or ""), str(secondary_answer))
    if ok:
        return {"verdict": VERDICT_PASS, "gate": "dual_verified",
                "reason": f"双源一致（{secondary_source}）"}
    return {"verdict": VERDICT_QUARANTINE, "gate": "dual_conflict",
            "reason": f"双源冲突：题库「{row.get('answer')}」≠ {secondary_source}「{secondary_answer}」"}


# ---------------------------------------------------------------- 总裁定
def judge_item(
    row: Dict[str, Any], *, known_hashes: Set[str], known_fps: Set[str],
    secondary_answer: Optional[str] = None,
) -> Dict[str, Any]:
    """三闸合并裁定：quarantine > pass > manual_review。"""
    checks = [
        dedup_check(row, known_hashes=known_hashes, known_fps=known_fps)
        or {"gate": "dedup_ok", "reason": "无重复"},
        consistency_check(row),
        dual_source_check(row, secondary_answer=secondary_answer),
    ]
    verdicts = [c.get("verdict") for c in checks if "verdict" in c]
    for target in (VERDICT_QUARANTINE, VERDICT_PASS, VERDICT_MANUAL):
        if target in verdicts:
            first = next(c for c in checks if c.get("verdict") == target)
            return {"verdict": target, "gate": first["gate"],
                    "reason": first["reason"], "checks": checks}
    raise AssertionError("unreachable: judge_item 必须有裁定")


def verdict_to_fields(
    verdict: str, *, base_tag: str, tag_manual: bool = False,
    gate: Optional[str] = None, reason: Optional[str] = None,
) -> Dict[str, str]:
    """裁定 → 需要写回 course_kb 的字段（空 dict = 不改）。

    三条入库链路统一走这里，差别只在两个参数：

    * ``base_tag``：来源前缀（``auto_crawl`` / ``teacher`` / ``import``），
      隔离后写成 ``<base_tag>:guard_quarantine``，保留前缀让既有谓词
      （回退、来源筛选、``!= "quarantine"`` 精确比较）继续生效；
    * ``tag_manual``：``manual_review`` 是否落 tag。机器抽取链路（采集）
      置 True——「未自动验证」是有用信号；人工来源链路（教师录题/Excel
      导入）置 False——这两条本来每题都强制人审，打标只会把 source_tag
      冲成噪音并丢掉来源语义。

    ``gate``/``reason`` 一并落 ``guard_gate``/``guard_reason``（迁移 0025）：
    异常队列页要靠它们说明「为什么被挡」，否则人工只能看到一条 quarantine
    却无从判断该放行还是该清。``pass`` 一律不改字段：自动验证通过就保持来源原样。
    """
    if verdict == VERDICT_QUARANTINE:
        fields = {"status": "quarantine", "source_tag": f"{base_tag}:{TAG_QUARANTINE}"}
    elif verdict == VERDICT_MANUAL and tag_manual:
        fields = {"source_tag": f"{base_tag}:{TAG_MANUAL}"}
    else:
        return {}
    if gate:
        fields["guard_gate"] = str(gate)[:32]
    if reason:
        fields["guard_reason"] = str(reason)[:500]
    return fields


def verdict_to_db(verdict: str, current_status: str = "draft") -> Dict[str, str]:
    """裁定 → 写库字段（存量脚本用；采集 hook 用 crawl 专属后缀）。"""
    if verdict == VERDICT_QUARANTINE:
        return {"status": "quarantine", "source_tag": TAG_QUARANTINE}
    if verdict == VERDICT_MANUAL:
        if current_status == "draft":
            return {"status": "draft", "source_tag": TAG_MANUAL}
        return {"status": current_status, "source_tag": f"{current_status}_untouched"}
    return {"status": current_status, "source_tag": "unchanged"}
