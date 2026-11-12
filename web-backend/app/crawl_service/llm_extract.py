"""M5-2：非结构化源（``url`` / ``web``）的「网页 -> 结构化题目」LLM 抽取环节。

链路位置
--------
``parse_items`` 是同步纯函数，遇到 HTML/纯文本只打 ``NEEDS_LLM`` 标记；真正的
模型调用在 async 编排层 ``scheduler._resolve_items_from_source`` 里转到本模块：

    fetch_source_content -> parse_items(标记 NEEDS_LLM)
        -> llm_extract.extract_items(content, ...)
            -> clean_html           清洗 + 截断（控 token）
            -> build_extract_prompt 组装提示词
            -> _default_transport   租户 BYOM 或全局 env 模型通道
            -> parse_extract_json   稳健剥离围栏/截取首末括号
            -> validate_extracted   schema 校验 + 复用 map_bank_record/normalize_item
        -> items 交回 run_job -> intake 去重入库 draft

设计约束（对应方案书 ADR / 市场差距分析 P0）
--------------------------------------------
* **绝不编造**：提示词要求只抽取原文真实存在的题；原文无题返回空数组；
  schema 校验丢弃无题干/无答案的题。宁可 0 条 + 明确原因，不造脏题。
* **不静默 0 条**：任何失败（未启用/未配模型/超时/非 JSON/全被拒）都返回可读
  ``reason``，写进 ``CrawlRun.error``，供 ``/metrics`` 与前端运行日志追溯。
* **成本可控**：正文按 ``max_chars`` 截断、最多抽 ``max_items`` 题、单次调用不重试
  （重试会把失败页反复计费）；模型不可用直接返回，不退化成模板题。
* **安全**：全局 env 的 ``base_url`` 过 ``core.ssrf.assert_safe_base_url`` 闸门；
  密钥只从租户加密配置或环境变量读取，不进代码/日志。
* **可测**：``transport`` 可注入 ``async (system, user) -> str``，单测不联网。
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings
from app.core.ssrf import SSRFRejected, assert_safe_base_url
from app.crawl_service import fetcher as crawl_fetch
from app.crawl_service.intake import normalize_item

log = logging.getLogger(__name__)

#: 提示词版本：输出结构/规则变更就 bump，便于按版本回看历史抽取质量。
LLM_EXTRACT_PROMPT_VERSION = 1

#: 抽取时整块丢弃的区域（导航/页脚/脚本对抽题只有噪声，还白烧 token）。
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "nav", "footer", "header",
    "aside", "form", "iframe", "template",
})
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "table", "ul", "ol",
})

LLM_EXTRACT_SYSTEM = (
    "你是课程题库的「结构化抽取器」，任务是把用户提供的网页正文中**真实存在的题目**"
    "转写成 JSON。你不是出题人。\n"
    "【硬性规则】\n"
    "1. 只能抽取原文里确实出现的题目；严禁编造、改写、补全、推测题目或答案。\n"
    "2. 原文没有题目（如纯资讯页/导航页）就返回 {\"questions\": []}，不要硬凑。\n"
    "3. 每道题必须同时给出 stem（题干）和 answer（答案）；缺任一项就跳过该题。\n"
    "4. 拿不准答案就跳过该题，不要猜；options 只写原文出现的选项。\n"
    "5. 只输出严格 JSON，不要 markdown 围栏、不要任何解释文字。\n"
    "【输出格式】\n"
    '{"questions":[{"stem":"题干","answer":"答案","type":"单选|多选|填空|解答",'
    '"options":["A. ...","B. ..."],"analysis":"解析","difficulty":3,'
    '"subject":"数学","grade":"高三","chapter":"章节",'
    '"knowledge_points":["知识点"],"paper_type":"卷别","source_province":"省份"}]}'
)


class _TextExtractor(HTMLParser):
    """只保留可见文本，跳过脚本/导航等噪声块，块级标签转换为换行。"""

    def __init__(self) -> None:
        # 实体统一在 clean_html 末尾反转义一次，避免 HTML 分支双重解码
        super().__init__(convert_charrefs=False)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        t = tag.lower()
        if t in _SKIP_TAGS:
            self._skip_depth += 1
        elif t in _BLOCK_TAGS and not self._skip_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif t in _BLOCK_TAGS and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def clean_html(content: str, *, max_chars: int = 0) -> str:
    """HTML -> 可见纯文本（保留题目换行），并按 ``max_chars`` 截断控 token。

    纯文本输入只做空白压缩，不误伤内容。``max_chars <= 0`` 表示不截断。
    """
    text = content or ""
    if re.search(r"<\s*[a-zA-Z][^>]*>", text):
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
            text = "".join(parser.parts)
        except Exception:  # noqa: BLE001 脏 HTML 不应让采集整批失败
            text = re.sub(r"<[^>]+>", " ", text)
    # HTML 与纯文本统一在此反转义一次（如 &gt; / &nbsp; / &#x3E;）
    text = html_lib.unescape(text)
    # 压缩行内空白、收敛连续空行，避免大段空格把 token 吃满
    text = re.sub(r"[ \t\u3000\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…[正文已截断]"
    return text


def build_extract_prompt(
    cleaned: str, *, source_type: str, max_items: int
) -> Tuple[str, str]:
    """返回 ``(system, user)``；正文作为数据传入，不拼进 system。"""
    user = (
        f"【源类型】{source_type or 'web'}\n"
        f"【最多抽取】{max_items} 题\n"
        f"【网页正文（已清洗，可能被截断）】\n"
        f"{cleaned}\n\n"
        "请按规则只输出 JSON。"
    )
    return LLM_EXTRACT_SYSTEM, user


def parse_extract_json(text: str) -> Any:
    """稳健解析模型输出：剥离任意位置围栏、截取首末 ``{}``/``[]``。

    模型常在 ```json 前加一句口语前言，或尾部追加说明，直接 ``json.loads`` 会失败；
    这里先尝试原样解析，再剥离围栏，最后按首末括号截取。
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("模型返回为空")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json|JSON)?\s*(.*?)```", body, flags=re.S)
    candidate = fenced.group(1).strip() if fenced else body
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # 无围栏或有前言/尾注：定位第一个 { 或 [ 与最后一个 } 或 ]
    starts = [p for p in (candidate.find("{"), candidate.find("[")) if p >= 0]
    ends = [p for p in (candidate.rfind("}"), candidate.rfind("]")) if p >= 0]
    if not starts or not ends or min(starts) >= max(ends):
        raise ValueError("模型输出中未找到完整 JSON 对象/数组")
    sliced = candidate[min(starts): max(ends) + 1]
    try:
        return json.loads(sliced)
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出不是合法 JSON：{exc}") from exc


def validate_extracted(
    payload: Any, *, max_items: int, tenant_id: str = "_probe"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """schema 校验并收敛成 ``intake`` 可入库的 items，逐条回报拒收原因。

    复用 ``fetcher.map_bank_record``（中->英/选项/难度归一）与
    ``intake.normalize_item``（题干+答案必须非空）判可入库性，保证抽取与 bank
    链路**同一口径**；不合规的单题丢弃，不让坏 1 题毁掉整批。
    """
    raw_items = payload.get("questions") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and not isinstance(raw_items, list):
        for key in ("items", "data", "list"):
            if isinstance(payload.get(key), list):
                raw_items = payload[key]
                break
    if not isinstance(raw_items, list):
        return [], [{"index": 0, "reason": "模型输出不是题目数组"}]

    out: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: set = set()
    for i, raw in enumerate(raw_items):
        if len(out) >= max_items:
            break
        if not isinstance(raw, dict):
            rejected.append({"index": i, "reason": "题目不是对象"})
            continue
        rec = crawl_fetch.map_bank_record(raw)
        stem = str(rec.get("stem") or "").strip()
        if stem in seen:
            rejected.append({"index": i, "reason": "同批重复题干"})
            continue
        if normalize_item(rec, tenant_id=tenant_id, grade="高三", subject="数学") is None:
            rejected.append({"index": i, "reason": "缺题干或答案（不编造）"})
            continue
        seen.add(stem)
        out.append(rec)
    return out, rejected


async def _default_transport(
    system: str,
    user: str,
    *,
    db: Any = None,
    tenant_id: str = "",
    timeout: float = 90.0,
) -> str:
    """模型通道：优先租户 BYOM（与 M4-3 组卷同款），否则全局 env 兜底。

    两条路径的 ``base_url`` 都过 SSRF 闸门；都没有配置则抛可读错误，
    **绝不退化成模板题或静默成功**。
    """
    import httpx

    from app.services import byom

    base_url = ""
    api_key = ""
    model = ""
    if db is not None and tenant_id:
        cfg = await byom.get_config(db, tenant_id)
        if cfg is not None and cfg.enabled and cfg.base_url and cfg.api_key_enc:
            base_url = cfg.base_url
            api_key = byom.decrypt_api_key(cfg.api_key_enc)
            model = cfg.model or ""
    if not (base_url and api_key):
        base_url = settings.crawl_llm_base_url
        api_key = settings.crawl_llm_api_key
        model = settings.crawl_llm_model
    if not (base_url and api_key):
        raise RuntimeError(
            "未配置可用模型通道（租户 BYOM 未启用且 CRAWL_LLM_BASE_URL/API_KEY 未设置）"
        )
    try:
        base_url = assert_safe_base_url(base_url)
    except SSRFRejected as exc:
        raise RuntimeError(f"模型 base_url 未通过 SSRF 闸门：{exc}") from exc
    if not model:
        raise RuntimeError("未配置模型名（CRAWL_LLM_MODEL 或租户 BYOM model）")

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "max_tokens": 4000,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"模型端点返回 {resp.status_code}")
    text = (
        (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
    ).strip()
    if not text:
        raise RuntimeError("模型返回内容为空")
    return text


async def extract_items(
    content: str,
    *,
    source_type: str,
    db: Any = None,
    tenant_id: str = "",
    transport=None,
    max_chars: Optional[int] = None,
    max_items: Optional[int] = None,
    timeout: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """M5-2 主入口：返回 ``(items, reason)``，``reason`` 非空表示未产出题目。

    ``transport`` 为 ``async (system, user) -> str``，注入后不联网（单测用）。
    任何异常都转成可读 ``reason``，交回 ``CrawlRun.error``，不静默 0 条。
    """
    on = settings.crawl_llm_extract_enabled if enabled is None else enabled
    if not on:
        return [], (
            f"{crawl_fetch.NEEDS_LLM}：LLM 抽取未启用"
            "（CRAWL_LLM_EXTRACT_ENABLED=false）"
        )
    limit_chars = int(
        settings.crawl_llm_max_chars if max_chars is None else max_chars
    )
    limit_items = max(1, int(
        settings.crawl_llm_max_items if max_items is None else max_items
    ))
    timeout_s = float(
        settings.crawl_llm_timeout if timeout is None else timeout
    )

    cleaned = clean_html(content, max_chars=limit_chars)
    if not cleaned:
        return [], "LLM 抽取失败：网页清洗后正文为空"
    system, user = build_extract_prompt(
        cleaned, source_type=source_type, max_items=limit_items
    )
    try:
        if transport is not None:
            text = await transport(system, user)
        else:
            text = await _default_transport(
                system, user, db=db, tenant_id=tenant_id, timeout=timeout_s
            )
        payload = parse_extract_json(text)
    except Exception as exc:  # noqa: BLE001 模型/网络/解析失败都要留可读原因
        log.warning("crawl llm extract failed: %s", exc)
        return [], f"LLM 抽取失败：{exc}"

    items, rejected = validate_extracted(
        payload, max_items=limit_items, tenant_id=tenant_id or "_probe"
    )
    if not items:
        return [], (
            f"LLM 抽取未得到可入库题目（拒收 {len(rejected)} 条："
            f"{'; '.join(r['reason'] for r in rejected[:3]) or '原文可能不含题目'}）"
        )
    log.info(
        "crawl llm extract: %d items, %d rejected, prompt_v=%s",
        len(items), len(rejected), LLM_EXTRACT_PROMPT_VERSION,
    )
    return items, None
