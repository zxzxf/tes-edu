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

    async def fake_llm(bundle, student_answer, **_kw):
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

    async def fake_llm(bundle, student_answer, **_kw):
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

    async def fake_llm(bundle, student_answer, **_kw):
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

    async def fake_llm(bundle, student_answer, **_kw):
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

    async def fake_judge_v2(_q, _a, context=None, **_kw):
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


# ------------------------------------------------- seed_steps BG（BYOM）

class _FakeResp:
    def __init__(self, payload: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": payload})()})()]


class _FakeCompletions:
    def __init__(self, sink: dict, payload: str):
        self.sink = sink
        self.payload = payload

    async def create(self, **kw):
        self.sink.update(kw)
        return _FakeResp(self.payload)


class _FakeClient:
    def __init__(self, sink: dict, payload: str, cfg):
        # 记录客户端构造参数：必须来自租户 cfg，而不是 config.LLM_*
        sink["client_kwargs"] = {"base_url": cfg.chat_base_url, "api_key": cfg.chat_api_key}
        self.chat = type("Chat", (), {"completions": _FakeCompletions(sink, payload)})()


class _FakeConn:
    def __init__(self, sink: list):
        self.sink = sink

    async def execute(self, sql, *args):
        self.sink.append((sql, args))


class _FakePool:
    def __init__(self, sink: list):
        self.sink = sink

    def acquire(self):
        conn = _FakeConn(self.sink)
        import contextlib

        @contextlib.asynccontextmanager
        async def _acq():
            yield conn

        return _acq()


def _seed_bundle():
    b = essay_bundle()
    b["steps"] = []
    b["analysis"] = "先求 C=π/3，再由正弦定理求边。"
    return b


def _patch_seed(monkeypatch, cfg, inserted: list):
    monkeypatch.setattr(judge_v2, "AUTO_SEED_STEPS", True)
    monkeypatch.setattr("app.core.config.db_ready", lambda: True)
    # 关键：全局 env 配成明显不同的值，一旦代码回退到 config.LLM_* 就会暴露
    monkeypatch.setattr("app.core.config.LLM_BASE_URL", "https://global.invalid/v1")
    monkeypatch.setattr("app.core.config.LLM_API_KEY", "GLOBAL-KEY")
    monkeypatch.setattr("app.core.config.LLM_MODEL", "global-model")

    async def fake_resolve(tenant_id=None, *, llm_cfg=None):
        return llm_cfg or cfg

    monkeypatch.setattr(judge_v2.tenant_llm, "resolve_for", fake_resolve)

    async def fake_pool():
        return _FakePool(inserted)

    import app.db.pg as pg
    monkeypatch.setattr(pg, "get_pool", fake_pool)


def test_seed_steps_uses_tenant_config(monkeypatch):
    """seed_steps_bg 必须用租户 cfg 建客户端（端点/密钥/模型），不得硬编码全局 env。"""
    from app.core.tenant_llm import LLMConfig

    cfg = LLMConfig(
        chat_base_url="https://tenant.example.com/v1", chat_api_key="TENANT-KEY",
        chat_model="tenant-model", embed_base_url="", embed_api_key="",
        embed_model="", embed_dim=1024, model_simple="", model_hard="",
        rag_top_k=5, source="tenant", tenant_id="school_pilot_01",
    )
    sink: list = []
    calls: dict = {}
    _patch_seed(monkeypatch, cfg, sink)

    async def _go():
        captured = {}
        monkeypatch.setattr(
            judge_v2.tenant_llm, "make_client",
            lambda c: _FakeClient(captured, '[{"step_no":1,"text":"求C"}]', c),
        )
        await judge_v2.seed_steps_bg("h1", _seed_bundle(), tenant_id="school_pilot_01")
        return captured

    captured = _run(_go())
    assert captured["client_kwargs"] == {
        "base_url": "https://tenant.example.com/v1", "api_key": "TENANT-KEY"
    }
    assert captured.get("model") == "tenant-model"
    assert sink and sink[0][1][0] == "h1"


def test_seed_steps_falls_back_to_global_when_no_tenant(monkeypatch):
    """未配租户时回落全局：仍走 tenant_llm 解析（cfg.source == global），不直连 config。"""
    from app.core.tenant_llm import LLMConfig

    cfg = LLMConfig(
        chat_base_url="https://global.invalid/v1", chat_api_key="GLOBAL-KEY",
        chat_model="global-model", embed_base_url="", embed_api_key="",
        embed_model="", embed_dim=1024, model_simple="", model_hard="",
        rag_top_k=5, source="global",
    )
    sink: list = []
    _patch_seed(monkeypatch, cfg, sink)
    seen: dict = {}

    async def _go():
        monkeypatch.setattr(
            judge_v2.tenant_llm, "make_client",
            lambda c: _FakeClient(seen, '[{"step_no":2,"text":"代入"}]', c),
        )
        await judge_v2.seed_steps_bg("h2", _seed_bundle())

    _run(_go())
    assert seen["client_kwargs"]["api_key"] == "GLOBAL-KEY"
    assert seen.get("model") == "global-model"


def test_seed_steps_skips_without_chat_model(monkeypatch):
    """chat 段三件套不齐 -> 静默跳过，不落库、不抛异常。"""
    from app.core.tenant_llm import LLMConfig

    cfg = LLMConfig(
        chat_base_url="https://tenant.example.com/v1", chat_api_key="K",
        chat_model="", embed_base_url="", embed_api_key="", embed_model="",
        embed_dim=1024, model_simple="", model_hard="", rag_top_k=5, source="tenant",
    )
    _patch_seed(monkeypatch, cfg, [])

    def explode(*_a, **_kw):
        raise AssertionError("chat_model 为空时不应构造客户端")

    monkeypatch.setattr(judge_v2.tenant_llm, "make_client", explode)
    _run(judge_v2.seed_steps_bg("h3", _seed_bundle(), tenant_id="t1"))


def test_spawn_seed_steps_passes_tenant_and_cfg(monkeypatch):
    """spawn 必须把 tenant_id / llm_cfg 透传下去（否则后台任务回落全局）。"""
    monkeypatch.setattr(judge_v2, "AUTO_SEED_STEPS", True)
    seen: dict = {}

    async def fake_seed(ch, bundle, *, tenant_id=None, llm_cfg=None):
        seen.update({"ch": ch, "tenant_id": tenant_id, "llm_cfg": llm_cfg})

    monkeypatch.setattr(judge_v2, "seed_steps_bg", fake_seed)

    async def _go():
        judge_v2.spawn_seed_steps("h4", _seed_bundle(), tenant_id="school_pilot_01",
                                  llm_cfg={"chat_model": "m"})
        await asyncio.sleep(0)

    _run(_go())
    assert seen["ch"] == "h4"
    assert seen["tenant_id"] == "school_pilot_01"
    assert seen["llm_cfg"] == {"chat_model": "m"}


# ------------------------------------------------- 主解答案自洽校验（ref_answer）

def test_ref_answer_matches_student_without_llm(monkeypatch):
    """无标注题 + 学生与主解答案一致 -> 直接判对，绝不调 LLM。

    回归：题面乱码时判分模型读不懂题面，会把"与系统答案一致"的作答判成错，
    学生看到「系统答 1、判我 1 是错的」。
    """
    monkeypatch.setattr(judge_v2, "load_bundle", lambda _h: None)
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)
    r = _run(judge_v2.judge("题", "a=1", context={"chunks": []}, ref_answer="1"))
    assert r["correct"] is True
    assert r["attribution_source"] == "ref_answer"


def test_ref_answer_equality_variants():
    """等价表述都要命中；不同数值不能误命中。"""
    for ref, stu in (("1", "a=1"), ("a=1", "1"), ("1", "1"), ("A", "选 A"), ("1", "等于1")):
        assert judge_v2.judge_by_reference_answer(ref, stu) is not None, (ref, stu)
    # 数值不同绝不能判对
    for ref, stu in (("1", "12"), ("1", "2"), ("2", "a=1")):
        assert judge_v2.judge_by_reference_answer(ref, stu) is None, (ref, stu)


def test_ref_answer_does_not_match_partial_variable():
    """ref='x=2' 时学生只答 'x' 不算对（等号左侧是变量名，不是答案）。"""
    assert judge_v2.judge_by_reference_answer("x=2", "x") is None
    assert judge_v2.judge_by_reference_answer("x=2", "2") is not None


def test_ref_answer_long_text_skipped(monkeypatch):
    """引导档的长提示语/整段解析不是"答案"，不能拿来做等价判定。"""
    monkeypatch.setattr(judge_v2, "load_bundle", lambda _h: None)
    guide = "我们先看看已知条件，你能说说第一步想怎么入手吗？把你的想法告诉我吧"
    assert judge_v2.judge_by_reference_answer(guide, "1") is None
    r = _run(judge_v2.judge("题", "1", context={"chunks": []}, ref_answer=guide + "，再多写一点"))
    assert r["attribution_source"] == "unknown"


def test_ref_answer_miss_still_unknown(monkeypatch):
    """自洽没命中就退回 unknown（由上层决定是否回退 v1），不能硬判错。"""
    monkeypatch.setattr(judge_v2, "load_bundle", lambda _h: None)
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)
    r = _run(judge_v2.judge("题", "a=2", context={"chunks": []}, ref_answer="1"))
    assert r["correct"] is None
    assert r["attribution_source"] == "unknown"


def test_bundle_answer_wins_over_ref_answer(monkeypatch):
    """有标注题时以标注为准——ref_answer 只在无标注题时兜底。"""
    _patch_bundle(monkeypatch, single_bundle())
    monkeypatch.setattr(judge_v2, "judge_by_llm", _explode)
    # 标注答案是 B；ref_answer 故意给个不一致的 A，仍须按标注判对
    r = _run(judge_v2.judge("题", "B", context=_ctx("h_single"), ref_answer="A"))
    assert r["attribution_source"] == "map"
    assert r["correct"] is True


# ------------------------------------------------- 题面清洗

def test_normalize_question_repairs_eaten_exponent():
    from app.core import orchestrator

    assert orchestrator.normalize_question("f(x)=x+2/e×+a") == "f(x)=x+2/e^x+a"
    # 真乘号不能被动
    assert orchestrator.normalize_question("2×3") == "2×3"
    assert orchestrator.normalize_question("a×b") == "a×b"
    assert orchestrator.normalize_question("x×y") == "x×y"


def test_fallback_comment_follows_v1_verdict(monkeypatch):
    """回退 v1 后文案必须跟结论一致：不能"判对了"却写"转人工复核"。

    回归：v2 unknown 的 comment 非空，v1 的点评被 `if not j['comment']` 挡掉，
    学生看到「判分结果·正确 / 未匹配到标注题，转人工复核」的自相矛盾。
    """
    from app.core import orchestrator

    async def fake_v2(_q, _a, context=None, **_kw):
        return judge_v2._unknown("未匹配到标注题（无 content_hash），转人工复核")

    async def fake_v1(_q, _a, context=None, **_kw):
        return {"correct": True, "partial": False, "comment": "答案与系统参考答案一致"}

    monkeypatch.setattr(orchestrator.judge_v2, "judge", fake_v2)
    monkeypatch.setattr(orchestrator.llm, "judge", fake_v1)

    r = _run(orchestrator._judge_v2_with_fallback("题", "a=1", {}))
    assert r["correct"] is True
    assert "转人工复核" not in r["comment"]
    assert r["comment"] == "答案与系统参考答案一致"


def test_fallback_passes_ref_answer_and_tenant(monkeypatch):
    """回退 v1 时必须带上主解答案与租户——否则判分与主解两套模型各判各的。"""
    from app.core import orchestrator
    seen: dict = {}

    async def fake_v2(_q, _a, context=None, **_kw):
        return judge_v2._unknown("未匹配到标注题")

    async def fake_v1(_q, _a, context=None, **kw):
        seen.update(kw)
        return {"correct": False, "partial": False, "comment": ""}

    monkeypatch.setattr(orchestrator.judge_v2, "judge", fake_v2)
    monkeypatch.setattr(orchestrator.llm, "judge", fake_v1)

    r = _run(orchestrator._judge_v2_with_fallback(
        "题", "a=2", {}, ref_answer="1", tenant_id="school_pilot_01"
    ))
    assert seen.get("ref_answer") == "1"
    assert seen.get("tenant_id") == "school_pilot_01"
    # v1 没给点评时回落中性说明，同样不能出现"转人工复核"
    assert "转人工复核" not in r["comment"]
