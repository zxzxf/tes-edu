"""S3 judge v2 三路分流测试。

覆盖计划书验收项：
- 三路（客观题查表 / 填空查表 / 解答 LLM）各 ≥2 用例
- distractor_map 命中**不触发 LLM** 的断言
- unknown 兜底断言（低置信不硬猜）
- 客观题查表路径延迟（纯查表，P95 <50ms）
"""
import asyncio
import time

from app.core import judge_v2


# ------------------------------------------------------------------ 夹具

def _step(no, kind="option", label="", text="", correct=None, **kw):
    return {
        "step_no": no,
        "step_kind": kind,
        "label": label,
        "text": text,
        "is_correct": correct,
        "explanation": "",
        "error_reason": "",
        "distractor_map": None,
        "wrong_value_map": None,
        "typical_errors": None,
        **kw,
    }


def single_bundle():
    """单选：答案 B；A 有干扰项映射，C/D 未标注。"""
    return {
        "content_hash": "h_single",
        "type": "单选",
        "answer": "B",
        "stem": "解方程 2x=4",
        "analysis": "",
        "options": {"A": "x=1", "B": "x=2", "C": "x=3", "D": "x=4"},
        "steps": [
            _step(1, label="A", text="x=1", correct=False,
                  distractor_map={"wrong_step": 1, "error_type": "calc",
                                  "reason": "把 2x=4 误算成 x=1", "correction": "应为 x=2"}),
            _step(2, label="B", text="x=2", correct=True),
            _step(3, label="C", text="x=3", correct=False),
            _step(4, label="D", text="x=4", correct=False),
        ],
    }


def multi_bundle():
    """多选：答案 AB。"""
    return {
        "content_hash": "h_multi",
        "type": "多选",
        "answer": "AB",
        "stem": "下列哪些正确",
        "analysis": "",
        "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
        "steps": [
            _step(1, label="A", text="甲", correct=True),
            _step(2, label="B", text="乙", correct=True),
            _step(3, label="C", text="丙", correct=False,
                  distractor_map={"wrong_step": 3, "error_type": "concept",
                                  "reason": "概念混淆", "correction": "丙不成立"}),
            _step(4, label="D", text="丁", correct=False),
        ],
    }


def blank_bundle():
    """填空：答案 1；错值 -1 有映射。"""
    return {
        "content_hash": "h_blank",
        "type": "填空",
        "answer": "1",
        "stem": "若 f 为偶函数，a = ___",
        "analysis": "",
        "options": {},
        "steps": [
            _step(1, kind="blank", label="第1空", text="a",
                  wrong_value_map=[{"value": "-1", "wrong_step": 1, "error_type": "concept",
                                    "reason": "忽略 x³ 的负号", "correction": "应为 1"}]),
        ],
    }


def essay_bundle():
    """解答：两步参考步骤。"""
    return {
        "content_hash": "h_essay",
        "type": "解答",
        "answer": "略",
        "stem": "证明题",
        "analysis": "先求 C 再求高",
        "options": {},
        "steps": [
            _step(1, kind="subq", label="(1)", text="求 C", explanation="由内角和"),
            _step(2, kind="subq", label="(2)", text="求高", explanation="正弦定理"),
        ],
    }


class _NoLLM(Exception):
    pass


async def _explode(*_a, **_kw):
    """若查表路径误触发 LLM，直接炸掉测试。"""
    raise _NoLLM("查表路径不应触发 LLM 调用")


def _ctx(h):
    return {"chunks": [{"content_hash": h, "text": "", "score": 0.9}]}


def _run(coro):
    return asyncio.run(coro)


def _patch_bundle(monkeypatch, bundle):
    async def fake_load(_hash):
        return bundle
    monkeypatch.setattr(judge_v2, "load_bundle", fake_load)


# ------------------------------------------------- 路径① 客观题查表

def test_objective_distractor_hit_does_not_call_llm(monkeypatch):
    """核心卖点：错项命中 distractor_map 即定案，零 token。"""
    _patch_bundle(monkeypatch, single_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "A", context=_ctx("h_single")))
    assert r["attribution_source"] == "map"
    assert r["correct"] is False
    assert r["first_wrong_step"] == 1
    assert r["error_type"] == "calc"
    assert "x=1" in r["evidence"]
    assert r["correction"] == "应为 x=2"


def test_objective_correct_answer_by_map(monkeypatch):
    _patch_bundle(monkeypatch, single_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "B", context=_ctx("h_single")))
    assert r["correct"] is True
    assert r["attribution_source"] == "map"
    assert r["error_type"] is None


def test_objective_unmapped_distractor_falls_back_to_llm(monkeypatch):
    """错项未标注 -> 交 LLM 兜底。"""
    _patch_bundle(monkeypatch, single_bundle())
    called = {}

    async def fake_llm(bundle, student_answer):
        called["hit"] = True
        return {"correct": False, "partial": False, "comment": "llm",
                "first_wrong_step": 3, "error_type": "unknown", "evidence": "",
                "correction": "", "attribution_source": "llm"}

    monkeypatch.setattr(judge_v2, "judge_by_llm", fake_llm)
    r = _run(judge_v2.judge("题", "C", context=_ctx("h_single")))
    assert called.get("hit") is True
    assert r["attribution_source"] == "llm"


def test_multi_missing_selection_is_unknown(monkeypatch):
    """多选漏选：查表无映射，不硬猜 -> unknown 入人工复核。"""
    _patch_bundle(monkeypatch, multi_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "A", context=_ctx("h_multi")))
    assert r["attribution_source"] == "unknown"
    assert r["error_type"] == "unknown"
    assert "漏选" in r["evidence"] and "B" in r["evidence"]


def test_multi_wrong_selection_hit(monkeypatch):
    _patch_bundle(monkeypatch, multi_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "AC", context=_ctx("h_multi")))
    assert r["attribution_source"] == "map"
    assert r["first_wrong_step"] == 3
    assert r["error_type"] == "concept"


def test_objective_map_path_latency_under_50ms(monkeypatch):
    """验收项：客观题纯查表 P95 <50ms。"""
    bundle = single_bundle()
    _patch_bundle(monkeypatch, bundle)
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    samples = []
    for _ in range(200):
        t0 = time.perf_counter()
        _run(judge_v2.judge("题", "A", context=_ctx("h_single")))
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 50, f"P95={p95:.2f}ms 超过 50ms"


# ------------------------------------------------- 路径② 填空查表

def test_blank_wrong_value_hit(monkeypatch):
    _patch_bundle(monkeypatch, blank_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "-1", context=_ctx("h_blank")))
    assert r["attribution_source"] == "map"
    assert r["correct"] is False
    assert r["error_type"] == "concept"
    assert r["first_wrong_step"] == 1


def test_blank_correct_answer(monkeypatch):
    _patch_bundle(monkeypatch, blank_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", " 1 ", context=_ctx("h_blank")))
    assert r["correct"] is True
    assert r["attribution_source"] == "map"


def test_blank_unmatched_falls_back_to_llm(monkeypatch):
    _patch_bundle(monkeypatch, blank_bundle())

    async def fake_llm(bundle, student_answer):
        return {"correct": False, "partial": False, "comment": "llm",
                "first_wrong_step": 1, "error_type": "calc", "evidence": "",
                "correction": "", "attribution_source": "llm"}

    monkeypatch.setattr(judge_v2, "judge_by_llm", fake_llm)
    r = _run(judge_v2.judge("题", "999", context=_ctx("h_blank")))
    assert r["attribution_source"] == "llm"


# ------------------------------------------------- 路径③ 解答 LLM

def test_essay_uses_llm(monkeypatch):
    _patch_bundle(monkeypatch, essay_bundle())
    called = {}

    async def fake_llm(bundle, student_answer):
        called["hit"] = True
        return {"correct": False, "partial": False, "comment": "第1步就错",
                "first_wrong_step": 1, "error_type": "concept", "evidence": "C=π/3",
                "correction": "", "attribution_source": "llm"}

    monkeypatch.setattr(judge_v2, "judge_by_llm", fake_llm)
    # 解答题要写够过程才会进入判分（S4 提交协议）
    r = _run(judge_v2.judge("题", "由 A+B+C=π 且 A+B=3C 得 4C=π，所以 C=π/3",
                            context=_ctx("h_essay")))
    assert called.get("hit") is True
    assert r["first_wrong_step"] == 1
    assert r["error_type"] == "concept"


def test_essay_without_llm_is_unknown(monkeypatch):
    """dev/无模型：解答题对不齐 -> unknown，不硬猜。"""
    _patch_bundle(monkeypatch, essay_bundle())
    monkeypatch.setattr("app.core.config.llm_ready", lambda: False)

    r = _run(judge_v2.judge("题", "我先假设 C=π/3，然后随便推了一下就得到答案了",
                            context=_ctx("h_essay")))
    assert r["attribution_source"] == "unknown"
    assert r["error_type"] == "unknown"
    assert r["correct"] is None


# ------------------------------------------------- 兜底与归一化

def test_no_content_hash_is_unknown(monkeypatch):
    """未匹配到标注题 -> unknown（不在请求路径上现造步骤）。"""
    r = _run(judge_v2.judge("题", "A", context={"chunks": []}))
    assert r["attribution_source"] == "unknown"
    assert r["correct"] is None


def test_empty_answer_is_unknown():
    r = _run(judge_v2.judge("题", "   ", context=_ctx("h")))
    assert r["attribution_source"] == "unknown"


# ------------------------------------------------- S4 过程采集提交协议

def test_empty_answer_needs_process_not_wrong():
    """空作答 = 要求补过程；correct 必须是 None，不能是 False（否则误记错题）。"""
    r = _run(judge_v2.judge("题", "", context=_ctx("h")))
    assert r["need_process"] is True
    assert r["correct"] is None
    assert r["error_type"] is None


def test_essay_short_answer_needs_process(monkeypatch):
    """解答题只写"略"：要求补过程，不判分。"""
    _patch_bundle(monkeypatch, essay_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "略", context=_ctx("h_essay")))
    assert r["need_process"] is True
    assert r["correct"] is None


def test_essay_with_process_goes_to_llm(monkeypatch):
    """写了足够过程 -> 正常进入 LLM 判分。"""
    _patch_bundle(monkeypatch, essay_bundle())
    called = {}

    async def fake_llm(bundle, student_answer):
        called["hit"] = True
        return {"correct": False, "partial": False, "comment": "第1步错",
                "first_wrong_step": 1, "error_type": "concept", "evidence": "",
                "correction": "", "attribution_source": "llm", "need_process": False}

    monkeypatch.setattr(judge_v2, "judge_by_llm", fake_llm)
    r = _run(judge_v2.judge("题", "因为 A+B=3C，所以 C=π/3（这是完整过程）",
                            context=_ctx("h_essay")))
    assert called.get("hit") is True
    assert r["need_process"] is False


def test_objective_short_answer_still_judged(monkeypatch):
    """客观题作答天然很短（"A"），不能被过程长度门槛误拦。"""
    _patch_bundle(monkeypatch, single_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)

    r = _run(judge_v2.judge("题", "A", context=_ctx("h_single")))
    assert r["need_process"] is False
    assert r["attribution_source"] == "map"


def test_orchestrator_skips_llm_fallback_when_need_process(monkeypatch):
    """要求补过程时不得退回 v1 判分：既白花 token，又可能把"没写过程"判成"做错"。"""
    from app.core import orchestrator

    async def fake_judge_v2(_q, _a, context=None):
        return {"correct": None, "partial": False, "comment": "请写过程",
                "first_wrong_step": None, "error_type": None, "evidence": "",
                "correction": "", "attribution_source": "unknown", "need_process": True}

    async def explode_v1(*_a, **_kw):
        raise AssertionError("need_process 时不应再调 v1 判分")

    monkeypatch.setattr(orchestrator.judge_v2, "judge", fake_judge_v2)
    monkeypatch.setattr(orchestrator.llm, "judge", explode_v1)

    r = _run(orchestrator._judge_v2_with_fallback("题", "", {}))
    assert r["need_process"] is True
    assert r["correct"] is None


def test_error_type_normalization():
    """模型爱输出中文，必须收敛到 taxonomy 英文值。"""
    assert judge_v2._norm_error_type("概念混淆") == "concept"
    assert judge_v2._norm_error_type("运算错误") == "calc"
    assert judge_v2._norm_error_type("公式") == "formula"
    assert judge_v2._norm_error_type("审题失误") == "read"
    assert judge_v2._norm_error_type("concept") == "concept"
    assert judge_v2._norm_error_type("") == "unknown"
    assert judge_v2._norm_error_type("乱七八糟") == "unknown"


def test_value_normalization():
    """全角/LaTeX 异写要能对齐。"""
    assert judge_v2._norm_value(" １／２ ") == judge_v2._norm_value("\\frac{1}{2}")
    assert judge_v2._norm_value("−1") == judge_v2._norm_value("-1")
    assert judge_v2._norm_value("２×３") == judge_v2._norm_value("2*3")


def test_labels_extraction():
    assert judge_v2._labels("选 A") == ["A"]
    assert judge_v2._labels("我认为是 b 和 d") == ["B", "D"]
    assert judge_v2._labels("多选 AB") == ["A", "B"]
    assert judge_v2._labels("没有选项") == []
