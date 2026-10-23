# -*- coding: utf-8 -*-
"""全题型步骤分解器（步骤级归因的数据基础）。

设计原则（用户 2026-10-23 拍板）：步骤结构必须覆盖**全题型**，不能只做解答题。

各题型的步骤化规则：
  单选/多选 : 每个选项一步（kind=option, label=A/B/C/D, is_correct=该字母是否在答案中）。
              多选天然支持"按选项部分计分"的归因（漏选/错选分别定位）。
  填空      : 按答案拆分（括号感知，只在括号深度 0 处按逗号/分号拆），每空一步
              （kind=blank, label=第N空）。兼容单空(2021-2024)与多空(2025-2026)。
  解答      : 有 sub_questions（人工细标）-> kind=subq，kp_source=manual；
              无细标 -> 从解析按（1）（2）拆 -> kind=procedure，kp 继承题级。
  实验      : 从解析按（1）（2）拆 -> kind=procedure，kp 继承题级。
  兜底      : 任何题型只要以上规则产出 0 步，就生成一步 kind=single（label=全题），
              保证 course_kb 里每道题至少 1 个步骤，归因永远有落点。

知识点（kp）挂载策略：
  - manual：数学解答题 sub_questions 自带 kp_primary(id+name+confidence)，是人工细标。
  - question：其余步骤继承题级主知识点（数学用 DAG id，物理用章节域中文名）。
    干扰项/选项级 KP 后续教研精标后升级为 manual，现在先保证"每步可归因"。
"""
import re

# 括号对（中英文 + LaTeX 常用），用于括号感知拆分
_PAIRS = {"(": ")", "（": "）", "[": "]", "【": "】", "{": "}", "〔": "〕"}
_CLOSERS = set(_PAIRS.values())

# 填空答案的顶层分隔符（括号内不拆，保护 "3x+4y-5=0（或 x=-1 或 ...）" 这类等价解）
_TOP_SEPS = ("，", ",", ";", "；", "、")


def split_top_level(s, seps=_TOP_SEPS):
    """只在括号深度 0 处按分隔符切分。"""
    parts, depth, cur = [], 0, ""
    for ch in str(s or ""):
        if ch in _PAIRS:
            depth += 1
        elif ch in _CLOSERS:
            depth = max(0, depth - 1)
        if ch in seps and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def parse_options(opts):
    """选项 -> [(label, text)]。数学是 list ['A. xxx']，物理是 dict {'A':'xxx'}。"""
    out = []
    if isinstance(opts, dict):
        for k in sorted(opts):
            out.append((str(k), str(opts[k]).strip()))
    elif isinstance(opts, list):
        for o in opts:
            o = str(o).strip()
            m = re.match(r"^([A-E])[\.、\s．:：]+(.*)$", o)
            if m:
                out.append((m.group(1), m.group(2).strip()))
            elif o:
                out.append(("", o))
    return [(lb, tx) for lb, tx in out if tx]


def answer_letters(ans):
    """从答案里提取选项字母（如 'B' / 'BC' / 'ACD'）。"""
    s = str(ans or "").upper()
    # 只在答案前 16 字符内找，避免把解析文本里的字母当答案
    return set(re.findall(r"[A-E]", s[:16]))


def split_analysis_subs(analysis):
    """从解析文本拆小问 -> [(label, kind)]。支持三类编号，取命中数最多的一类：
      （1）（2）/(1)(2)  阿拉伯编号
      （i）（ii）       罗马数字（实验题常用）
      A．B．C．         逐选项分析式（label='选项A'）
    至少 2 个标记才拆，避免文中偶发引用误拆。
    """
    a = str(analysis or "")
    cands = []
    m_ar = list(re.finditer(r"[（(]\s*([1-9])\s*[）)]", a))
    if len(m_ar) >= 2:
        cands.append([(f"({m.group(1)})", m.start(), m.end()) for m in m_ar])
    m_ro = list(re.finditer(r"[（(]\s*([ivx]{1,4})\s*[）)]", a, re.I))
    if len(m_ro) >= 2:
        cands.append([(f"({m.group(1)})", m.start(), m.end()) for m in m_ro])
    m_op = list(re.finditer(r"(?m)^\s*([A-E])[．.]", a))
    if len(m_op) >= 2:
        cands.append([(f"选项{m.group(1)}", m.start(), m.start() + 3) for m in m_op])
    if not cands:
        return []
    best = max(cands, key=len)
    subs = []
    for i, (label, s, e) in enumerate(best):
        end = best[i + 1][1] if i + 1 < len(best) else len(a)
        seg = a[e:end].strip()
        if seg:
            subs.append((label, seg))
    return subs if len(subs) >= 2 else []


def split_answer_segs(ans):
    """答案结构拆分（解析缺失时的兜底步骤源）-> [seg, ...]。
    支持 ①②③ / (1)(2) / 行首 1. 2. / 顶层分号逗号（括号感知）。"""
    a = str(ans or "").strip()
    if not a:
        return []
    marks = list(re.finditer(r"①|②|③|④|⑤|⑥|[（(]\s*[1-9]\s*[）)]", a))
    marks += list(re.finditer(r"^\s*[1-9][．、]", a, re.M))
    marks.sort(key=lambda m: m.start())
    if len(marks) >= 2:
        segs = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(a)
            seg = a[m.end():end].strip(" ；;，,。")
            if seg:
                segs.append(seg)
        if len(segs) >= 2:
            return segs
    segs = split_top_level(a)
    return segs if len(segs) >= 2 else []


def build_steps(rec, dag_names=None, subject="数学"):
    """生成一道题的全题型步骤列表。

    rec        : clean JSON 原始记录（含 type/options/answer/analysis/sub_questions/kp_primary）
    dag_names  : {kp_id: node}，数学传 DAG 用于查 name；物理传 None
    返回       : [{step_no, step_kind, label, text, is_correct, kp_code, kp_name, kp_source, confidence}]
    """
    dag_names = dag_names or {}
    t = str(rec.get("type", "")).strip()
    ans = str(rec.get("answer", "")).strip()

    # ---- 题级主知识点（question 级步骤继承它） ----
    kpp = rec.get("kp_primary") or {}
    if subject == "数学" and kpp.get("id"):
        q_code, q_name = kpp.get("id"), kpp.get("name") or ""
    else:
        q_code, q_name = "", str(rec.get("knowledge_point") or "").strip()

    def q_step():
        return {"kp_code": q_code or None, "kp_name": q_name or None,
                "kp_source": "question", "confidence": None}

    steps = []
    if t in ("single", "multi", "choice"):
        letters = answer_letters(ans)
        for label, text in parse_options(rec.get("options")):
            st = q_step()
            st.update({"step_kind": "option", "label": label, "text": text,
                       "is_correct": (label in letters) if label else None})
            steps.append(st)
    elif t in ("fill", "blank"):
        segs = split_top_level(ans) or ([ans] if ans else [])
        for i, seg in enumerate(segs, 1):
            st = q_step()
            st.update({"step_kind": "blank", "label": f"第{i}空", "text": seg,
                       "is_correct": None})
            steps.append(st)
    elif t in ("solve", "essay", "experiment"):
        subs = rec.get("sub_questions") or []
        if t in ("solve", "essay") and subs:
            for sq in subs:
                k = sq.get("kp_primary") or {}
                steps.append({"step_kind": "subq", "label": f"({sq.get('sub_no')})",
                              "text": str(sq.get("text") or "").strip(), "is_correct": None,
                              "kp_code": k.get("id") or None, "kp_name": k.get("name") or None,
                              "kp_source": "manual", "confidence": sq.get("confidence")})
        else:
            # 优先级：解析编号拆（阿拉伯/罗马/逐选项分析）> 答案结构拆 > single 兜底
            for label, text in split_analysis_subs(rec.get("analysis")):
                st = q_step()
                st.update({"step_kind": "procedure", "label": label, "text": text,
                           "is_correct": None})
                steps.append(st)
            if not steps:
                for i, seg in enumerate(split_answer_segs(ans), 1):
                    st = q_step()
                    st.update({"step_kind": "blank", "label": f"第{i}答",
                               "text": seg, "is_correct": None})
                    steps.append(st)
    # 其他题型（含未知类型）走兜底

    # ---- 兜底：全题型保证每题至少 1 步 ----
    if not steps:
        st = q_step()
        st.update({"step_kind": "single", "label": "全题",
                   "text": str(rec.get("stem", ""))[:300], "is_correct": None})
        steps.append(st)

    for i, st in enumerate(steps, 1):
        st["step_no"] = i
        st.setdefault("label", "")
        st.setdefault("text", "")
    return steps


def steps_stat(steps_list):
    """统计用：{kind: count}。"""
    from collections import Counter
    return dict(Counter(s["step_kind"] for s in steps_list))
