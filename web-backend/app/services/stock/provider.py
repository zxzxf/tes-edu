"""行情数据源适配层（docx/股票盯盘业务中心实施计划.md v1.4 §2.1/五附）。

免费裸接口，后端代理（浏览器直连有 Referer/跨域限制）：
- 报价主源 新浪 hq.sinajs.cn/list=（需 Referer finance.sina.com.cn，GBK 编码）；
- 报价备源 腾讯 qt.gtimg.cn/q=（~ 分隔，GBK）；
- K线/分时 东方财富 push2his（JSON，secid=市场.代码：sh=1、sz=0）。

工程兜底（计划书 §七"数据源抖动"）：Provider 全 async httpx、超时不抛穿；
调用方拿 {} / None 视为源不可用 → 展示最后快照 + fetched_at 时间戳，绝不 500。

symbol 规范：小写带市场前缀，sh600519 / sz000858 / sh000001（指数）；
纯 6 位数字自动判市场：6/9→sh，0/2/3→sz，4/8→bj。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("stock.provider")

SINA_URL = "https://hq.sinajs.cn/list="
TENCENT_URL = "https://qt.gtimg.cn/q="
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_TREND_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EM_DETAIL_URL = "https://push2.eastmoney.com/api/qt/stock/get"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": _UA}
_TENCENT_HEADERS = {"User-Agent": _UA}

#: 大盘指数（盯盘总览固定看这几只；新浪代码即 symbol）
INDEX_SYMBOLS = ["sh000001", "sz399001", "sh000300", "sz399006"]


def normalize_symbol(raw: str) -> Optional[str]:
    """归一 symbol：补市场前缀、统一小写；非法返回 None。"""
    s = (raw or "").strip().lower()
    if re.fullmatch(r"(sh|sz|bj)\d{6}", s):
        return s
    if re.fullmatch(r"\d{6}", s):
        head = s[0]
        if head in "69":
            return f"sh{s}"
        if head in "023":
            return f"sz{s}"
        if head in "48":
            return f"bj{s}"
    if s.startswith("us") or s.startswith("hk"):
        return None  # 本期只做 A 股（计划书 §六-2）
    return None


def em_secid(symbol: str) -> Optional[str]:
    """东财 secid：市场前缀换数字（sh=1, sz/bj=0）。"""
    if symbol.startswith("sh"):
        return f"1.{symbol[2:]}"
    if symbol.startswith(("sz", "bj")):
        return f"0.{symbol[2:]}"
    return None


def _f(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_sina(symbol: str, line: str) -> Optional[dict]:
    """hq_str_sh600519="名称,今开,昨收,现价,最高,最低,竞买,交易,成交量(股),成交额(元),..." """
    m = re.search(r'="(.*)"', line)
    if not m or not m.group(1):
        return None
    parts = m.group(1).split(",")
    if len(parts) < 9:
        return None
    prev_close = _f(parts[2])
    price = _f(parts[3])
    pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    q = {
        "symbol": symbol, "name": parts[0],
        "price": price, "prev_close": prev_close, "open": _f(parts[1]),
        "high": _f(parts[4]), "low": _f(parts[5]),
        "volume": _f(parts[8]), "amount": _f(parts[9]),
        "pct": pct, "change": round(price - prev_close, 4),
        "quote_time": f"{parts[30]} {parts[31]}" if len(parts) > 31 else parts[-2],
    }
    # 五档盘口（个股详情页用）：parts[10..19] 买五（量,价对）、[20..29] 卖五；
    # 量单位股→手（/100，与东财 f47 口径一致）；
    # 字段不足（测试样本行/异常行情）时缺省不填，前端显示"暂不支持"。
    if len(parts) >= 30:
        q["depth"] = {
            "bids": [{"price": _f(parts[11 + i * 2]), "volume": _f(parts[10 + i * 2]) / 100}
                     for i in range(5)],
            "asks": [{"price": _f(parts[21 + i * 2]), "volume": _f(parts[20 + i * 2]) / 100}
                     for i in range(5)],
        }
    return q


def _parse_tencent(symbol: str, line: str) -> Optional[dict]:
    """v_sh600519="1~贵州茅台~600519~现价~昨收~今开~量~...~[30]时间~[31]涨跌~[32]涨跌%~[33]高~[34]低~..." """
    m = re.search(r'="(.*)"', line)
    if not m or not m.group(1):
        return None
    parts = m.group(1).split("~")
    if len(parts) < 35:
        return None
    price, prev_close = _f(parts[3]), _f(parts[4])
    pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    return {
        "symbol": symbol, "name": parts[1],
        "price": price, "prev_close": prev_close, "open": _f(parts[5]),
        "high": _f(parts[33]), "low": _f(parts[34]),
        "volume": _f(parts[36]) * 100 if len(parts) > 36 else 0.0,  # 手→股
        "amount": _f(parts[37]) * 10000 if len(parts) > 37 else 0.0,  # 万→元
        "pct": pct, "change": round(price - prev_close, 4),
        "quote_time": parts[30] if len(parts) > 30 else "",
    }


async def _http_get(url: str, headers: dict, timeout: float = 6.0) -> str:
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        body = resp.content
    try:
        return body.decode("gbk")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


async def fetch_quotes(symbols: list[str], timeout: float = 6.0) -> dict[str, dict]:
    """批量实时报价：新浪主源 → 腾讯备源。全失败返回 {}（不抛，调用方降级）。"""
    syms = [s for s in (normalize_symbol(x) for x in symbols) if s]
    if not syms:
        return {}
    out: dict[str, dict] = {}
    try:
        text = await _http_get(SINA_URL + ",".join(syms), _SINA_HEADERS, timeout)
        for line in text.splitlines():
            m = re.match(r"var hq_str_((?:sh|sz|bj)\d{6})=", line)
            if not m:
                continue
            sym = m.group(1)
            q = _parse_sina(sym, line)
            if q:
                out[sym] = q
    except Exception as e:  # noqa: BLE001
        logger.warning("新浪行情失败（转腾讯备源）：%s", e)

    missing = [s for s in syms if s not in out]
    if missing:
        try:
            text = await _http_get(TENCENT_URL + ",".join(missing), _TENCENT_HEADERS, timeout)
            for line in text.splitlines():
                m = re.match(r'v_((?:sh|sz|bj)\d{6})="', line)
                if not m:
                    continue
                sym = m.group(1)
                q = _parse_tencent(sym, line)
                if q and sym not in out:
                    out[sym] = q
        except Exception as e:  # noqa: BLE001
            logger.warning("腾讯行情备源也失败：%s", e)
    return out


async def fetch_kline(symbol: str, period: str = "day", limit: int = 120,
                      timeout: float = 8.0) -> dict:
    """K线（东财）：period=day/week/month；返回 {name, items:[{date,open,close,high,low,volume}]}。"""
    sym = normalize_symbol(symbol)
    secid = em_secid(sym) if sym else None
    if not secid:
        return {"name": "", "items": []}
    klt = {"day": "101", "week": "102", "month": "103"}.get(period, "101")
    # 东财 lmt 在本接口不生效（实测仍返全量历史），用 beg 起始日期控制窗口：
    # 按周期把请求天数放宽冗余（节假日/停牌），Python 侧再截尾到 limit。
    limit = max(10, min(int(limit), 500))
    back_days = {"day": limit * 2 + 15, "week": limit * 16 + 30, "month": limit * 62 + 60}[period]
    beg = (datetime.now() - timedelta(days=back_days)).strftime("%Y%m%d")
    url = (f"{EM_KLINE_URL}?secid={secid}&klt={klt}&fqt=1"
           f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
           f"&beg={beg}&end=20500101")
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _UA}) as client:
            resp = await client.get(url)
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("东财K线失败 %s：%s", sym, e)
        return {"name": "", "items": []}
    d = (data or {}).get("data") or {}
    items = []
    for row in d.get("klines") or []:
        p = row.split(",")
        if len(p) < 6:
            continue
        items.append({
            "date": p[0], "open": _f(p[1]), "close": _f(p[2]),
            "high": _f(p[3]), "low": _f(p[4]), "volume": _f(p[5]),
        })
    return {"name": d.get("name") or "", "items": items[-limit:]}


async def fetch_intraday(symbol: str, timeout: float = 8.0) -> dict:
    """当日分时（东财 trends2）：返回 {name, prev_close, items:[{time,price,avg,volume}]}。"""
    sym = normalize_symbol(symbol)
    secid = em_secid(sym) if sym else None
    if not secid:
        return {"name": "", "prev_close": 0.0, "items": []}
    url = (f"{EM_TREND_URL}?secid={secid}&iscr=0&ndays=1"
           f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8&fields2=f51,f53,f56,f58")
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _UA}) as client:
            resp = await client.get(url)
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("东财分时失败 %s：%s", sym, e)
        return {"name": "", "prev_close": 0.0, "items": []}
    d = (data or {}).get("data") or {}
    items = []
    for row in d.get("trends") or []:
        p = row.split(",")
        if len(p) < 4:
            continue
        items.append({"time": p[0][-5:], "price": _f(p[1]),
                      "volume": _f(p[2]), "avg": _f(p[3])})
    return {"name": d.get("name") or "", "prev_close": _f(str(d.get("preClose", ""))),
            "items": items}


def _scaled(v, digits: int) -> float:
    """东财价格族定点数：按 f59 小数位还原（茅台 f59=2 → 143254/100）。"""
    try:
        return round(float(v) / (10 ** digits), digits)
    except (TypeError, ValueError):
        return 0.0


async def fetch_detail(symbol: str, timeout: float = 8.0) -> Optional[dict]:
    """个股详情（东财 stock/get）：基本面指标 + 涨跌停价，一次请求。

    价格族按 f59 小数位还原；比率族（PE/PB/换手/振幅/量比/涨跌幅）固定 ÷100。
    源失败返回 None（调用方降级），字段缺失取 0 不抛。
    """
    sym = normalize_symbol(symbol)
    secid = em_secid(sym) if sym else None
    if not secid:
        return None
    fields = ("f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f59,f60,"
              "f116,f117,f162,f167,f168,f169,f170,f171")
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _UA}) as client:
            resp = await client.get(f"{EM_DETAIL_URL}?secid={secid}&fields={fields}")
            d = (resp.json() or {}).get("data")
    except Exception as e:  # noqa: BLE001
        logger.warning("东财详情失败 %s：%s", sym, e)
        return None
    if not d:
        return None
    digits = int(d.get("f59") or 2)
    return {
        "symbol": sym, "name": d.get("f58") or "",
        "price": _scaled(d.get("f43"), digits),
        "high": _scaled(d.get("f44"), digits), "low": _scaled(d.get("f45"), digits),
        "open": _scaled(d.get("f46"), digits), "prev_close": _scaled(d.get("f60"), digits),
        "volume": _f(str(d.get("f47") or 0)),        # 手
        "amount": _f(str(d.get("f48") or 0)),        # 元
        "limit_up": _scaled(d.get("f51"), digits), "limit_down": _scaled(d.get("f52"), digits),
        "total_mv": _f(str(d.get("f116") or 0)),     # 元
        "float_mv": _f(str(d.get("f117") or 0)),
        "pe_ttm": round(_f(str(d.get("f162") or 0)) / 100, 2),
        "pb": round(_f(str(d.get("f167") or 0)) / 100, 2),
        "turnover_rate": round(_f(str(d.get("f168") or 0)) / 100, 2),   # %
        "change": round(_f(str(d.get("f169") or 0)) / 100, 2),
        "pct": round(_f(str(d.get("f170") or 0)) / 100, 2),             # %
        "amplitude": round(_f(str(d.get("f171") or 0)) / 100, 2),       # %
        "volume_ratio": round(_f(str(d.get("f50") or 0)) / 100, 2),
    }
