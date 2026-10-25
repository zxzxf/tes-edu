"""PII 脱敏纯函数（T-AM8 安全校验）。

与 Go 网关 `gateway/internal/audit/pii.go` 的 MaskPII 行为对齐（同一套规则、
同一套掩码形状），保证审计日志（网关卡）与业务响应（本模块）脱敏口径一致：

    13812345678       → 138****5678
    110101199001011234 → 110101********1234
    student@xx.com    → s*****t@xx.com

特性：
- 幂等：对已脱敏字符串再脱敏不变（重复脱敏不会进一步损坏）。
- 中文安全：正则带 \\b 边界，中文题干/姓名不误伤（有对应用例锁定）。
- 纯函数、不连库，便于单测与在任何序列化出口复用。
"""

import re
from typing import Any

# 手机号（大陆 11 位，1[3-9] 开头）
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 身份证（18 位：17 位数字 + 数字/X）
_RE_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# 邮箱
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask_phone(s: str) -> str:
    """手机号保留前 3 后 4。"""
    return _RE_PHONE.sub(lambda mo: mo.group(0)[:3] + "****" + mo.group(0)[7:], s)


def mask_id_card(s: str) -> str:
    """身份证保留前 6 后 4。"""
    return _RE_ID_CARD.sub(lambda mo: mo.group(0)[:6] + "********" + mo.group(0)[14:], s)


def mask_email(s: str) -> str:
    """邮箱保留首尾字符与域名。"""

    def _mask(mo: str) -> str:
        at = mo.find("@")
        if at <= 0:
            return mo
        local, domain = mo[:at], mo[at:]
        if len(local) <= 1:
            return "*" + domain
        if len(local) == 2:
            return local[:1] + "*" + domain
        return local[:1] + "*" * (len(local) - 2) + local[-1] + domain

    return _RE_EMAIL.sub(lambda mo: _mask(mo.group(0)), s)


def mask_text(s: Any) -> Any:
    """对任意文本统一脱敏（手机/身份证/邮箱）；非字符串原样返回。

    幂等：MaskPII("s*****t@xx.com") 不再变化（星号破坏正则匹配）。
    """
    if not isinstance(s, str):
        return s
    s = mask_phone(s)
    s = mask_id_card(s)
    s = mask_email(s)
    return s


def mask_free_text_fields(obj: Any, keys: tuple) -> Any:
    """递归遍历 dict/list，对命中 keys 的字符串字段做 mask_text。

    用于报告类响应：题干/步骤标签/建议等自由文本可能夹带 PII（如应用题里的
    手机号），对外返回前统一过一遍。规则：
    - dict：命中 keys 的字符串值脱敏；其余递归。
    - list：字符串元素直接脱敏（周报里列表字符串均为自由文本：suggestions、
      knowledge_points 等）；dict 元素递归。
    - 结构字段（student_id / 计数 / 日期 / 比例）不在 keys 内，原样保留。
    """
    if isinstance(obj, dict):
        return {
            k: (mask_text(v) if (k in keys and isinstance(v, str)) else mask_free_text_fields(v, keys))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_text(v) if isinstance(v, str) else mask_free_text_fields(v, keys) for v in obj]
    return obj
