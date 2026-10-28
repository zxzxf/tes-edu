"""判分「歧义题重算」红线测试（2026-10-29 线上误判事故回归）。

事故：真机拍照题面被 OCR 成 ``f(x)=x+2/e^x+a``（原意实为分式
``(x+2)/(e^x+a)``，正确答案 **a=1**）。学生作答 ``a=1`` 却被判 correct=False。

根因链：
1. 题干的分数线信息在排版/OCR 中丢失，纯文本 ``A/B+C`` 存在两种合法解读；
2. solve 侧已识别歧义，给出「若为…则 a=1；若为…则无解」的双解读答案；
3. **判分侧完全不知道这件事**——它不带系统解答，让模型自己重算一遍，模型按
   字面解读算出 a=-ln2（comment 里自相矛盾地写了 a=0 / a=1? / a=-ln2 三个值），
   于是把学生正确的 a=1 判为错误；
4. 更糟：judge_v2 返回的是 ``correct=None``（未匹配标注题 → 转人工复核），
   v1 兜底的 False 把它覆盖掉，最终回包是
   「correct=false + 未匹配到标注题，转人工复核」——自相矛盾且冤枉学生。

修复：
- 本轮 solve 的答案以 ``ref_answer`` 透传给判分，作为提示词里优先级最高的判据；
- ``_reconcile_with_ref`` 硬校验：模型判 False，但学生作答与系统解答的
  「标识符=值」精确一致时，改判 True（模型重算在歧义题上不可靠，不可信）。

红线：**系统已给出某个答案、学生照它答了，就绝不能判错。**
"""
import asyncio

import pytest

from app.core import config, llm, orchestrator


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ _kv_pairs

def test_kv_pairs_extracts_identifier_value():
    """从答案文本里抽出「标识符=值」对。"""
    assert llm._kv_pairs("a=1") == {"a": "1"}
    assert llm._kv_pairs("则 a=1；")["a"] == "1"


def test_kv_pairs_handles_subscript_and_chinese():
    """下标与中文变量名（a_5、S_n、面积）都要能抽出来。"""
    assert llm._kv_pairs("a_5=11")["a_5"] == "11"
    assert llm._kv_pairs("S_n=2n-1")["s_n"] == "2n-1"


def test_kv_pairs_ignores_noise_when_no_equals():
    assert llm._kv_pairs("无解") == {}


# ------------------------------------------------------ _reconcile_with_ref

REF_AMBIGUOUS = "若为 f(x)=(x+2)/(e^x+a)，则 a=1；若为 f(x)=x+2/e^x+a，则无解。"


def test_reconcile_rescues_correct_answer_judged_wrong():
    """事故场景：模型重算判 False，但学生答的就是系统给的值 -> 改判 True。"""
    v = {"correct": False, "partial": False, "comment": "正确答案应为 a=0（模型瞎算）"}
    out = llm._reconcile_with_ref(v, "a=1", REF_AMBIGUOUS)
    assert out["correct"] is True
    assert "系统解答一致" in out["comment"]


def test_reconcile_does_not_rescue_when_value_differs():
    """学生答的值与系统解答不一致 -> 不干预（真答错就该错）。"""
    v = {"correct": False, "partial": False, "comment": "算错了"}
    out = llm._reconcile_with_ref(v, "a=2", REF_AMBIGUOUS)
    assert out["correct"] is False
    assert out["comment"] == "算错了"


def test_reconcile_noop_when_already_correct():
    """已判 True 的不动（无需纠正，避免改坏 comment）。"""
    v = {"correct": True, "partial": False, "comment": "很好"}
    assert llm._reconcile_with_ref(v, "a=1", REF_AMBIGUOUS)["comment"] == "很好"


def test_reconcile_noop_without_ref_answer():
    """没有系统解答（如 hint 轮）时不做任何猜测。"""
    v = {"correct": False, "partial": False, "comment": "x"}
    assert llm._reconcile_with_ref(v, "a=1", None)["correct"] is False
    assert llm._reconcile_with_ref(v, "a=1", "")["correct"] is False


def test_reconcile_ignores_student_answer_without_kv():
    """学生只写了「1」这种裸值 —— 无法与系统解答做键匹配，不强行放行。"""
    v = {"correct": False, "partial": False, "comment": "x"}
    assert llm._reconcile_with_ref(v, "1", REF_AMBIGUOUS)["correct"] is False


def test_reconcile_matches_only_same_identifier():
    """键不同不算命中：系统说 x=1，学生答 a=1 —— 不能算对。"""
    v = {"correct": False, "partial": False, "comment": "x"}
    assert llm._reconcile_with_ref(v, "a=1", "解得 x=1")["correct"] is False


# --------------------------------------------------- 提示词带上系统解答

def test_remote_judge_prompt_carries_system_answer(monkeypatch):
    """系统解答必须进提示词，否则模型还会换个解读重算。"""
    seen = {}

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        async def create(self, **kw):
            seen["messages"] = kw["messages"]
            return _Resp('{"correct": true, "partial": false, "comment": "ok"}')

    class _Client:
        def __init__(self, *a, **k):
            self.chat = type("C", (), {"completions": _Completions()})()

    monkeypatch.setattr(llm.tenant_llm, "make_client", _Client)
    monkeypatch.setattr(llm, "thinking_kwargs", lambda mode: {})
    monkeypatch.setattr(llm.usage, "record_usage_bg", lambda **kw: None)

    _run(
        llm._remote_judge(
            "已知函数f(x)=x+2/e^x+a的最大值为1，则a=", "a=1", {"chunks": []}, ref_answer=REF_AMBIGUOUS
        )
    )
    joined = "".join(str(m.get("content", "")) for m in seen["messages"])
    assert REF_AMBIGUOUS in joined, "提示词里必须带上系统本轮解答"
    assert "优先级" in joined, "必须明确系统解答优先级高于模型重算"


def test_judge_system_requires_multi_reading_acceptance():
    """提示词必须写明：歧义题学生命中任一解读即判对。"""
    assert "任意一种解读" in llm._JUDGE_SYSTEM


# ----------------------------------------------- orchestrator 透传 ref_answer

def test_orchestrator_passes_ref_answer_to_judge(monkeypatch):
    """solve 算出的答案要透传给判分，否则判分仍在裸重算。"""
    captured = {}

    async def fake_retrieve(q, subject=None, *, llm_cfg=None):
        return {"chunks": [], "points": [], "steps": []}

    async def fake_solve(q, **kw):
        return {"answer": REF_AMBIGUOUS, "steps": [], "degraded": False}

    # 注意：judge_v2.judge 新增了 ref_answer 透传，测试桩必须一并接受
    async def fake_judge_v2(question, student_answer, context=None, llm_cfg=None,
                            ref_answer=None):
        return {
            "correct": None, "partial": False,
            "comment": "未匹配到标注题（无 content_hash），转人工复核",
            "attribution_source": "unknown", "error_type": "unknown",
            "first_wrong_step": None, "evidence": "", "correction": "",
            "need_process": False,
        }

    async def fake_llm_judge(question, student_answer, context=None, *, tenant_id=None,
                             llm_cfg=None, ref_answer=None):
        captured["ref_answer"] = ref_answer
        return {"correct": True, "partial": False, "comment": "与系统解答一致"}

    monkeypatch.setattr(orchestrator.rag, "retrieve", fake_retrieve)
    monkeypatch.setattr(orchestrator.llm, "solve", fake_solve)
    monkeypatch.setattr(orchestrator.judge_v2, "judge", fake_judge_v2)
    monkeypatch.setattr(orchestrator.llm, "judge", fake_llm_judge)
    monkeypatch.setattr(
        orchestrator.qtype, "route_question_tier",
        lambda ctx: {"tier": "medium", "tier_name": "中等", "qtype": "单选", "max_rounds": 2},
    )
    monkeypatch.setattr(config, "SIMILAR_ON_SOLVE", False)

    out = _run(orchestrator.handle_question("f(x)=x+2/e^x+a 最大值1求a", student_answer="a=1"))
    assert captured.get("ref_answer") == REF_AMBIGUOUS
    # 端到端：学生答对就不能再是 False
    assert out["judgement"]["correct"] is True
    # unknown 分支的「转人工复核」comment 必须被 v1 结论替换，不能自相矛盾
    assert "转人工复核" not in out["judgement"]["comment"]
