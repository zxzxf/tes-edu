"""出题 → 入库 的清洗/结构化层。

把教研 Excel/JSON/采购题库里的多种字段命名，统一成 QuestionIn 约定字段，
并计算 content_hash 用于幂等去重。
"""
import hashlib
import re
from typing import Dict, List, Optional


_ALIASES = {
    "subject": ["subject", "学科", "科目", "course"],
    "grade": ["grade", "年级", "学段"],
    "chapter": ["chapter", "章节", "章"],
    "type": ["type", "题型", "类型", "qtype"],
    "stem": ["stem", "题干", "题目", "question", "content", "title"],
    "options": ["options", "选项", "choices"],
    "answer": ["answer", "答案", "ans"],
    "analysis": ["analysis", "解析", "解答", "solution", "explain"],
    "difficulty": ["difficulty", "难度"],
    "paper_type": ["paper_type", "卷别", "paper", "exam_type"],
    "source_province": ["source_province", "来源", "省份", "source", "province"],
    "knowledge_points": ["knowledge_points", "知识点", "kp", "knowledge", "tags"],
}

# 单选/多选必须分开：组卷要控制题型配比，自适应要区分难度模型；
# 混成"选择"会让这两件事都做不了。与 agent-backend/scripts/import_bank.py 保持一致。
_TYPE_NORM = {
    "单选": "单选", "single": "单选", "single_choice": "单选",
    "多选": "多选", "multi": "多选", "multiple": "多选", "multiple_choice": "多选",
    "选择": "选择", "choice": "选择",        # 来源未区分时的兜底
    "实验": "实验", "experiment": "实验",
    "填空": "填空", "completion": "填空", "fill": "填空",
    "解答": "解答", "计算": "解答", "解答题": "解答", "solve": "解答", "essay": "解答",
    "压轴": "压轴", "压轴题": "压轴",
}


def _build_lookup(raw: Dict) -> Dict[str, str]:
    lut = {}
    for k, v in raw.items():
        if v in (None, ""):
            continue
        lut[str(k).strip().lower()] = v
    return lut


def _pick(lut: Dict[str, str], aliases: List[str]):
    # 返回原始值（可能是 str/list），由 _as_list / _as_options 负责类型归一；
    # 此处不做 str() 包裹，否则 list 类型会被压成字符串。
    for a in aliases:
        if a.lower() in lut:
            return lut[a.lower()]
    return None


def _as_list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [p.strip() for p in v.replace("、", ",").replace("，", ",").split(",") if p.strip()]
    return [str(v)]


def _as_options(v) -> Optional[List[str]]:
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        # 支持 "A.xxx B.yyy" 或 "A.xxx，B.yyy" 形式
        parts = re.split(r"(?=[A-Za-z]\.)", v)
        opts = [p.strip(" .") for p in parts if p.strip()]
        return opts or [v]
    return None


def normalize_question(raw: Dict) -> Dict:
    lut = _build_lookup(raw)
    subject = _pick(lut, _ALIASES["subject"]) or "未知"
    raw_type = _pick(lut, _ALIASES["type"])
    qtype = _TYPE_NORM.get(str(raw_type or "").strip(), "解答") if raw_type else "解答"
    stem = (_pick(lut, _ALIASES["stem"]) or "").strip()
    if not stem:
        raise ValueError("缺少题干（stem/题干/题目）")
    answer = _pick(lut, _ALIASES["answer"])
    # 题干+题型+答案 指纹，保证同题重复导入幂等
    fp = f"{stem}|{qtype}|{answer or ''}".encode("utf-8")
    content_hash = hashlib.sha256(fp).hexdigest()
    return {
        "subject": str(subject),
        "grade": _pick(lut, _ALIASES["grade"]),
        "chapter": _pick(lut, _ALIASES["chapter"]),
        "type": qtype,
        "stem": stem,
        "options": _as_options(_pick(lut, _ALIASES["options"])),
        "answer": answer,
        "analysis": _pick(lut, _ALIASES["analysis"]),
        "difficulty": int(_pick(lut, _ALIASES["difficulty"]) or 3),
        "paper_type": _pick(lut, _ALIASES["paper_type"]),
        "source_province": _pick(lut, _ALIASES["source_province"]),
        "knowledge_points": _as_list(_pick(lut, _ALIASES["knowledge_points"])),
        "content_hash": content_hash,
    }


def parse_records(raw_list: List[Dict]) -> List[Dict]:
    """批量清洗；跳过无法解析的行，返回统一结构列表。"""
    out: List[Dict] = []
    for r in raw_list:
        try:
            out.append(normalize_question(r))
        except (ValueError, TypeError):
            continue
    return out
