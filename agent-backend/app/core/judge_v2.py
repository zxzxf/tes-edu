"""judge v2：三路分流判分（S3 算法核心）。

分流规则（按 course_kb.type）：
  1) 客观题（单选/多选）：学生所选错项 -> 查 course_kb_steps.distractor_map，
     **命中即定案，不调 LLM**（零 token、毫秒级）。
  2) 填空题：作答值归一化 -> 匹配 wrong_value_map，命中即定案。
  3) 解答题：LLM 对齐参考 steps，输出首错步骤 + 错因。

统一输出（向后兼容 v1 的 correct/partial/comment）：
    {
      correct, partial, comment,
      first_wrong_step, error_type, evidence, correction, attribution_source
    }

attribution_source 语义：
    "map"     —— 查表命中（客观题零 LLM 成本，dev/无模型也能跑）
    "llm"     —— LLM 对齐判定
    "unknown" —— 低置信/对不齐，**不硬猜**，入人工复核队列
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from typing import Any

from app.core import config, llm, rag, tenant_llm

log = logging.getLogger("judge_v2")

# taxonomy 七类 + unknown（见 步骤级归因标注Schema-v1）
TAXONOMY = ("concept", "formula", "case", "calc", "read", "logic", "strategy")
UNKNOWN = "unknown"
#: 归因来源：无标注题时以本次主解答案为判据（区别于 map/llm）
REF_SOURCE = "ref_answer"

# LLM 判定超时：本项目用的是推理型模型，思考会占 30s+，给足预算但别拖死请求
JUDGE_TIMEOUT = int(config._get("JUDGE_TIMEOUT", "90"))
# 无标注题是否现场生成步骤并落库（跑一题攒一题）。默认关闭：
# 请求路径上写生产库 + 增加数十秒延迟，代价大于收益，交给 gen_pedagogy 批量跑更稳。
AUTO_SEED_STEPS = config._get("JUDGE_AUTOSEED", "0") == "1"

OBJECTIVE_TYPES = ("单选", "多选")
BLANK_TYPES = ("填空",)
# 需要提交"过程"的题型（S4 过程采集）：只给结论不给过程，无法做步骤级归因
PROCESS_TYPES = ("解答", "实验", "证明")
# 解答类题型的过程文本下限：短于此视为"只写了个结论"（如"略""不会"），不判分
MIN_PROCESS_LEN = int(config._get("MIN_PROCESS_LEN", "12"))

_JUDGE_V2_SYSTEM = (
    "你是一名高中数学阅卷老师，做【步骤级归因】判分。\n"
    "依据【参考步骤】逐条比对【学生作答】，找出学生**第一个出错的步骤**。\n"
    "规则：\n"
    "1) first_wrong_step 填参考步骤的 step_no（整数）；全对填 null。\n"
    "2) error_type 只能取这些英文值之一：concept(概念理解) / formula(公式误用) / "
    "case(分类讨论遗漏) / calc(计算运算) / read(审题漏条件) / logic(逻辑跳步) / "
    "strategy(方法策略) / unknown(无法判断)。**必须输出英文原值，不要输出中文**。\n"
    "3) evidence 摘录学生作答中支撑该判断的原文片段（≤80 字）。\n"
    "4) 证据不足或对不齐时，error_type 填 unknown，**不要硬猜**。\n"
    "5) 只输出严格 JSON，不要包裹 markdown："
    '{"correct":true/false,"first_wrong_step":<int|null>,'
    '"error_type":"<七类|unknown>","evidence":"...","comment":"一句话点评"}'
)

_SEED_SYSTEM = (
    "你把一道题的解析拆成有序的参考步骤，用于后续的作答比对。\n"
    "只输出严格 JSON 数组，不要包裹 markdown："
    '[{"step_no":1,"text":"步骤摘要","answer":"该步应得结果"}]\n'
    "要求：步骤数 2-6 步，按解题顺序；text 简洁（≤60 字）。"
)


# ---------------------------------------------------------------- 基础工具

def _norm(s: str) -> str:
    """字符串归一化：去空白与常见标点（复用 llm 模块口径，保证一致）。"""
    s = (s or "").strip().lower()
    return re.sub(r"[\s，,。.;；:：、()（）\[\]{}]+", "", s)


_TRANS = {
    "×": "*", "✕": "*", "·": "*", "÷": "/", "－": "-", "—": "-",
    "−": "-", "＋": "+", "＝": "=", "（": "(", "）": ")", "，": ",",
}


def _norm_value(s: str) -> str:
    """填空作答值归一化：统一全角/异体运算符，去空格与 LaTeX 装饰。

    先走 NFKC：中文输入法极易产出全角字符（１／２、Ａ、（）），不统一则比对全失配。
    """
    s = unicodedata.normalize("NFKC", s or "").strip().lower()
    for k, v in _TRANS.items():
        s = s.replace(k, v)
    s = s.replace("$", "").replace("\\left", "").replace("\\right", "")
    s = re.sub(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", s)
    s = re.sub(r"\s+", "", s)
    return s.rstrip("。.")


def _labels(s: str) -> list[str]:
    """从作答中抽取选项字母（A/B/C/D，大小写不敏感，去重保序）。"""
    found = re.findall(r"[A-Da-d]", s or "")
    out: list[str] = []
    for x in found:
        u = x.upper()
        if u not in out:
            out.append(u)
    return out


def _j(v: Any) -> Any:
    """asyncpg 把 jsonb 返回成 str，这里统一解析。"""
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return None


def _as_list(v: Any) -> list[dict]:
    v = _j(v)
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []


def _norm_error_type(v: Any) -> str:
    """把模型可能输出的中文/异写收敛到 taxonomy 英文值。"""
    s = (str(v or "")).strip().lower()
    if not s:
        return UNKNOWN
    if s in TAXONOMY or s == UNKNOWN:
        return s
    table = (
        ("concept", ("概念", "定义", "定理", "性质", "理解错", "混淆概念")),
        ("formula", ("公式", "关系式", "用错条件", "定理误用")),
        ("case", ("分类讨论", "漏讨论", "漏解", "漏情形", "讨论不全")),
        ("calc", ("计算", "运算", "算错", "化简", "抄错", "符号错误")),
        ("read", ("审题", "漏条件", "看错", "忽略", "未注意")),
        ("logic", ("逻辑", "循环论证", "充要", "跳步", "取交集")),
        ("strategy", ("方法", "策略", "思路", "选错工具", "方向")),
    )
    for key, words in table:
        if any(w in s for w in words):
            return key
    return UNKNOWN


#: 题面经 LLM 生成的"答案"超过这个长度基本是引导语/整段解析，不是可比对的值
REF_ANSWER_MAX_LEN = 40


def _norm_ans(s: str) -> str:
    """答案归一化：去掉 LaTeX 噪声、空白、大小写与常见标点，便于等价比较。"""
    s = (s or "").strip()
    s = re.sub(r"\\(left|right|displaystyle|quad|qquad|,|;|:|!)\b", "", s)
    s = s.replace("$", "").replace("\\ ", "").replace("\\,", "")
    s = re.sub(r"\s+", "", s)
    s = s.lower()
    s = re.sub(r"[，,。.;；:：、()（）\[\]{}【】「」『』\"'“”‘’]+", "", s)
    return s


#: 作答常见的中文引导词：「等于1」「答案为2」「选 A」——剥掉后才是真正的答案值
_ANS_LEAD = re.compile(
    r"^(等于|答案为|答案|结果是|结果|答|是|应选|选了|选|填写|填|为|：|:)"
)


def _strip_lead(s: str) -> str:
    """剥掉作答里的中文引导词（最多两轮，覆盖「答案为选 A」这类叠写）。"""
    for _ in range(2):
        s2 = _ANS_LEAD.sub("", s)
        if s2 == s:
            break
        s = s2
    return s


def _ans_candidates(s: str) -> list[str]:
    """答案候选值。含等号时**优先取最右段**：「a=1」的答案是 1，不是 a。

    同时保留整体串，使 ref='a=1' 与 stu='a=1' 也能直接相等命中。
    """
    n = _norm_ans(s)
    if not n:
        return []
    parts = [p for p in re.split(r"[=＝]", n) if p]
    if len(parts) > 1:
        return [_strip_lead(parts[-1]), _strip_lead(n)]
    return [_strip_lead(n)]


def judge_by_reference_answer(ref_answer: str | None, student_answer: str) -> dict | None:
    """路径⓪：无标注题时，用**本次主解给出的答案**做确定性自洽校验。

    为什么必须有这条：库里没有这道题时，判分只能靠 LLM 现推。而题面常带 OCR 乱码
    （实测「f(x)=x+2/e×+a」被压掉指数与分式结构），判分模型读不懂就直接判错——
    连系统自己刚生成的正确答案也一起判错，学生看到"系统答 1、判我 1 是错的"。
    主解答案就是展示给学生的答案，与它一致即判对，先于任何 LLM 调用。

    未命中（学生确实答了别的值）返回 None，交由 LLM 兜底。
    """
    ref = (ref_answer or "").strip()
    # 太长/多行 = 引导档提示语或整段解析，不是"答案"，不能拿来做等价判定
    if not ref or len(ref) > REF_ANSWER_MAX_LEN or "\n" in ref:
        return None
    rc, sc = _ans_candidates(ref), _ans_candidates(student_answer)
    if not rc or not sc:
        return None
    if any(r == s for r in rc for s in sc):
        return _ok(f"作答与本题参考答案一致（{ref}）", source=REF_SOURCE)
    return None


def _ok(comment: str, source: str = "map") -> dict:
    return {
        "correct": True,
        "partial": False,
        "comment": comment,
        "first_wrong_step": None,
        "error_type": None,
        "evidence": "",
        "correction": "",
        "attribution_source": source,
        "need_process": False,
    }


def _unknown(comment: str, evidence: str = "", first_wrong_step: int | None = None) -> dict:
    return {
        "correct": None,
        "partial": False,
        "comment": comment,
        "first_wrong_step": first_wrong_step,
        "error_type": UNKNOWN,
        "evidence": evidence,
        "correction": "",
        "attribution_source": UNKNOWN,
        "need_process": False,
    }


def _need_process(comment: str) -> dict:
    """S4 提交协议：解答类题型没贴过程 -> 明确要求补过程，而不是误判成"做错"。"""
    return {
        "correct": None,          # 注意：None 而非 False，避免污染掌握度
        "partial": False,
        "comment": comment,
        "first_wrong_step": None,
        "error_type": None,
        "evidence": "",
        "correction": "",
        "attribution_source": UNKNOWN,
        "need_process": True,
    }


def _pick(entry_v: Any, label: str | None = None) -> dict | None:
    """distractor_map 可能存成对象或数组；有 label 时优先取对应项。"""
    items = _as_list(entry_v)
    if not items:
        return None
    if label:
        for it in items:
            if str(it.get("label", "")).strip().upper() == label.upper():
                return it
    return items[0]


# ---------------------------------------------------------------- 数据读取

async def load_bundle(content_hash: str) -> dict | None:
    """取题目 + 步骤（S3 判分所需的全部燃料）。"""
    if not content_hash or not config.db_ready():
        return None
    from app.db.pg import get_pool  # 延迟导入：dev 模式不连库

    pool = await get_pool()
    async with pool.acquire() as conn:
        q = await conn.fetchrow(
            "SELECT type, answer, options, stem, analysis, difficulty FROM course_kb WHERE content_hash=$1",
            content_hash,
        )
        if q is None:
            return None
        rows = await conn.fetch(
            "SELECT step_no, step_kind, label, text, is_correct, explanation, error_reason,"
            "       distractor_map, wrong_value_map, typical_errors "
            "FROM course_kb_steps WHERE content_hash=$1 ORDER BY step_no",
            content_hash,
        )
    steps = [
        {
            "step_no": r["step_no"],
            "step_kind": r["step_kind"],
            "label": (r["label"] or "").strip(),
            "text": r["text"] or "",
            "is_correct": r["is_correct"],
            "explanation": r["explanation"] or "",
            "error_reason": r["error_reason"] or "",
            "distractor_map": _j(r["distractor_map"]),
            "wrong_value_map": _j(r["wrong_value_map"]),
            "typical_errors": _j(r["typical_errors"]),
        }
        for r in rows
    ]
    return {
        "content_hash": content_hash,
        "type": q["type"] or "",
        "answer": q["answer"] or "",
        "stem": q["stem"] or "",
        "analysis": q["analysis"] or "",
        # T-C3 题型路由：难度（course_kb.difficulty，线上整数 1-5；
        # qtype.difficulty_is_hard 归一化）——压轴判定信号之一
        "difficulty": q["difficulty"],
        "options": _j(q["options"]) or {},
        "steps": steps,
    }


def _pick_content_hash(context: dict | None) -> str | None:
    """从 RAG 上下文里取命中的题目 hash——**只认达到相似度门槛的可信命中**。

    ⚠️ 原实现是「取第一条带 hash 的 chunk」，不看分数。但 ``rag.retrieve`` 按
    余弦排序**永远返回 TOP_K 条**，库里没有这类题时也会硬凑：学生输入「1+1等于」
    命中一道复数方程真题（score 仅 0.5347），于是判分拿着复数题的标准答案（C）
    去判「等于2」，得出 correct=False——把正确答案判成错误。
    现在低分一律返回 None，走「未匹配到标注题 -> unknown（correct=None）」分支，
    宁可不判分转人工复核，也绝不用错题的答案判分。
    """
    top = rag.top_match(context)
    return (top or {}).get("content_hash") or None


# ---------------------------------------------------------------- 三路分流

def judge_objective_by_map(bundle: dict, student_answer: str) -> dict | None:
    """路径①：客观题查 distractor_map。未命中返回 None 交 LLM 兜底。"""
    correct_labels = _labels(bundle["answer"])
    sel = _labels(student_answer)
    if not correct_labels or not sel:
        return None
    opt_steps = [s for s in bundle["steps"] if s["step_kind"] == "option"]
    if set(sel) == set(correct_labels):
        return _ok(f"作答 {''.join(sel)} 与标准答案一致（查表判定）")

    # 误选：按步骤顺序取第一个被选中的错误选项
    for lb in sel:
        if lb in correct_labels:
            continue
        st = next((s for s in opt_steps if s["label"].upper() == lb), None)
        if st is None:
            continue
        dm = _pick(st["distractor_map"], lb)
        if not dm:
            return None  # 该错项未标注 -> 交给 LLM
        return {
            "correct": False,
            "partial": False,
            "comment": str(dm.get("reason") or "")[:200],
            "first_wrong_step": dm.get("wrong_step") or st["step_no"],
            "error_type": _norm_error_type(dm.get("error_type")),
            "evidence": f"学生选 {lb}（{st['text'][:60]}）",
            "correction": str(dm.get("correction") or ""),
            "attribution_source": "map",
            "need_process": False,
        }

    # 所选皆正确但漏选（多选常见）：查表无映射，不硬猜 -> unknown
    missing = [l for l in correct_labels if l not in sel]
    if missing:
        step_no = next(
            (s["step_no"] for s in opt_steps if s["label"].upper() == missing[0]), None
        )
        return _unknown(
            "漏选正确项，查表无对应映射，转人工复核",
            evidence=f"漏选 {'/'.join(missing)}，标准答案 {'/'.join(correct_labels)}",
            first_wrong_step=step_no,
        )
    return None


def judge_blank_by_map(bundle: dict, student_answer: str) -> dict | None:
    """路径②：填空查 wrong_value_map。未命中返回 None 交 LLM 兜底。"""
    ans = _norm_value(bundle["answer"])
    stu = _norm_value(student_answer)
    if not stu:
        return None
    if ans and (stu == ans or stu in _split_blanks(ans)):
        return _ok("作答与标准答案一致（查表判定）")
    for s in bundle["steps"]:
        for w in _as_list(s["wrong_value_map"]):
            if _norm_value(str(w.get("value", ""))) == stu:
                return {
                    "correct": False,
                    "partial": False,
                    "comment": str(w.get("reason") or "")[:200],
                    "first_wrong_step": w.get("wrong_step") or s["step_no"],
                    "error_type": _norm_error_type(w.get("error_type")),
                    "evidence": f"学生填 {student_answer.strip()[:60]}",
                    "correction": str(w.get("correction") or ""),
                    "attribution_source": "map",
                    "need_process": False,
                }
    return None


def _split_blanks(ans: str) -> list[str]:
    """多空答案按常见分隔符切分（已归一化，故只留半角分隔）。"""
    return [x for x in re.split(r"[;；,，/]", ans) if x]


async def judge_by_llm(bundle: dict, student_answer: str, *, llm_cfg=None) -> dict:
    """路径③：LLM 对齐参考步骤（客观题兜底 / 解答题主路径）。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。
    """
    if not config.llm_ready():
        return _unknown("查表未命中且未配置 LLM，无法判定，转人工复核")

    ref = "\n".join(
        f"step{s['step_no']}[{s['step_kind']}] {s['text']}"
        + (f" | 要点：{s['explanation'][:80]}" if s["explanation"] else "")
        for s in bundle["steps"]
    ) or "（该题暂无参考步骤，请按通法判断）"

    user = (
        f"【题目】\n{bundle['stem'][:1200]}\n\n"
        f"【标准答案】{bundle['answer']}\n\n"
        f"【参考步骤】\n{ref[:2000]}\n\n"
        f"【学生作答】\n{student_answer[:1200]}"
    )
    try:
        cfg = llm_cfg or await tenant_llm.resolve_llm_config(None)
        client = tenant_llm.make_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.chat_model,
            messages=[
                {"role": "system", "content": _JUDGE_V2_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
            timeout=JUDGE_TIMEOUT,
            **llm.thinking_kwargs("judge"),
        )
        data = _j(resp.choices[0].message.content or "") or {}
        if not isinstance(data, dict) or "correct" not in data:
            return _unknown("LLM 未按格式返回，转人工复核")
        step = data.get("first_wrong_step")
        return {
            "correct": bool(data.get("correct")),
            "partial": False,
            "comment": str(data.get("comment") or "")[:200],
            "first_wrong_step": int(step) if isinstance(step, (int, float)) else None,
            "error_type": _norm_error_type(data.get("error_type")),
            "evidence": str(data.get("evidence") or "")[:200],
            "correction": "",
            "attribution_source": "llm",
            "need_process": False,
        }
    except Exception as e:  # noqa: BLE001 判分失败不得拖垮主链路
        return _unknown(f"LLM 判定失败（{type(e).__name__}），转人工复核")


# ---------------------------------------------------------------- 入口

async def judge(
    question: str,
    student_answer: str,
    context: dict | None = None,
    *,
    llm_cfg=None,
    ref_answer: str | None = None,
) -> dict:
    """judge v2 统一入口：先查表，查不到再 LLM，都不行则 unknown。

    S4 提交协议：解答类题型必须先贴过程文本，否则返回 need_process=true 要求补过程，
    **不判分**（correct 保持 None，避免把"没写过程"记成"做错了"）。

    W6 BYOM T-M4：``llm_cfg`` 透传租户级模型配置（回落全局）。

    ``ref_answer``：本次主解给出的答案。仅在**无标注题**时用作确定性自洽判据
    （见 :func:`judge_by_reference_answer`）；有标注题一律以标注为准。
    """
    answer = (student_answer or "").strip()
    if not answer:
        return _need_process("请填写你的作答（解答题需写出过程）后再提交判分")

    content_hash = _pick_content_hash(context)
    bundle = await load_bundle(content_hash) if content_hash else None
    if bundle is None:
        # 无标注题：不在请求路径上现造步骤（见 AUTO_SEED_STEPS 说明）。
        # 先用主解答案自洽校验，避免"系统答 1 却判学生 1 错"的自相矛盾。
        hit = judge_by_reference_answer(ref_answer, answer)
        if hit is not None:
            return hit
        return _unknown("未匹配到标注题（无 content_hash），转人工复核")

    qtype = bundle["type"]
    # 解答/实验/证明类：只写结论不写过程 -> 要求补过程，不判分
    if any(t in qtype for t in PROCESS_TYPES) and len(answer) < MIN_PROCESS_LEN:
        return _need_process(
            f"解答题需写出解题过程（不少于 {MIN_PROCESS_LEN} 字），只写结论无法定位首错步骤"
        )
    if any(t in qtype for t in OBJECTIVE_TYPES):
        hit = judge_objective_by_map(bundle, student_answer)
        if hit is not None:
            return hit
    elif any(t in qtype for t in BLANK_TYPES):
        hit = judge_blank_by_map(bundle, student_answer)
        if hit is not None:
            return hit

    # 解答题 / 查表未命中 -> LLM
    return await judge_by_llm(bundle, student_answer, llm_cfg=llm_cfg)


async def seed_steps_bg(
    content_hash: str,
    bundle: dict,
    *,
    tenant_id: str | None = None,
    llm_cfg=None,
) -> None:
    """无标注题：后台用 analysis 现生成参考步骤并落库（跑一题攒一题）。

    默认关闭（JUDGE_AUTOSEED=1 开启）。启用时走后台任务，不阻塞判分响应。

    BYOM：模型/端点/密钥一律走 ``tenant_llm`` 解析（``llm_cfg`` 透传优先，
    否则按 ``tenant_id`` 查租户配置，未配才回落平台全局 env）。
    改造前这里硬编码 ``config.LLM_*``，全球局密钥失效或租户未走平台端点时会全败。
    """
    if not AUTO_SEED_STEPS or not bundle or bundle["steps"]:
        return
    # 注意：不再用 config.llm_ready() 卡门——那只校验平台全局 env，
    # 纯 BYOM 部署（租户自带端点、全局未配 LLM）会被误判为不可用。
    # 真正的可用性由解析出的 cfg（chat 段三件套）决定。
    if not (config.db_ready() and bundle["analysis"]):
        return
    try:
        cfg = await tenant_llm.resolve_for(tenant_id, llm_cfg=llm_cfg)
        if not (cfg.chat_base_url and cfg.chat_api_key and cfg.chat_model):
            log.info("seed_steps 跳过：租户 %s 无可用 chat 配置", tenant_id)
            return
        client = tenant_llm.make_client(cfg)
        resp = await client.chat.completions.create(
            model=cfg.chat_model,
            messages=[
                {"role": "system", "content": _SEED_SYSTEM},
                {
                    "role": "user",
                    "content": f"【题目】\n{bundle['stem'][:1200]}\n\n【解析】\n{bundle['analysis'][:1500]}",
                },
            ],
            temperature=0,
            timeout=JUDGE_TIMEOUT,
            **llm.thinking_kwargs("seed"),
        )
        items = _j(resp.choices[0].message.content or "") or []
        if not isinstance(items, list) or not items:
            return
        from app.db.pg import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            for it in items[:8]:
                await conn.execute(
                    "INSERT INTO course_kb_steps(content_hash, step_no, step_kind, text) "
                    "VALUES($1,$2,'procedure',$3) ON CONFLICT DO NOTHING",
                    content_hash,
                    int(it.get("step_no") or 0),
                    str(it.get("text") or "")[:500],
                )
    except Exception as e:  # noqa: BLE001 攒步骤是锦上添花，失败静默
        log.warning("seed_steps 失败（不影响判分）: %s: %s", type(e).__name__, e)


def spawn_seed_steps(
    content_hash: str,
    bundle: dict,
    *,
    tenant_id: str | None = None,
    llm_cfg=None,
) -> None:
    """后台派发步骤生成。``llm_cfg`` 由判分主链路透传，避免重复解析租户配置。"""
    if not AUTO_SEED_STEPS:
        return
    asyncio.create_task(
        seed_steps_bg(content_hash, bundle, tenant_id=tenant_id, llm_cfg=llm_cfg)
    )
