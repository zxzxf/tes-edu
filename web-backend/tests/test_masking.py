"""PII 脱敏纯函数单测（T-AM8）。

镜像网关 gateway/internal/audit/pii_test.go 的用例，锁定 web-backend 与网关
脱敏口径一致：手机前3后4 / 身份证前6后4 / 邮箱首尾+域名 / 幂等 / 中文不误伤。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.masking import (
    mask_email,
    mask_free_text_fields,
    mask_id_card,
    mask_phone,
    mask_text,
)


def test_mask_phone():
    got = mask_phone("学生联系电话 13812345678 已记录")
    assert "138****5678" in got
    assert "13812345678" not in got


def test_mask_id_card():
    got = mask_id_card("证件号 110101199001011234")
    assert "110101********1234" in got
    assert "110101199001011234" not in got


def test_mask_email():
    got = mask_email("联系 student@example.com")
    assert "s*****t@example.com" in got
    assert "student@example.com" not in got


def test_mask_email_short_local():
    assert mask_email("a@b.com") == "*@b.com"
    assert mask_email("ab@cd.com") == "a*@cd.com"


def test_mask_idempotent():
    once = mask_text("13812345678 110101199001011234 a@b.com")
    twice = mask_text(once)
    assert once == twice


def test_mask_keeps_chinese_and_plain():
    plain = "2026/10/21 22:00 127.0.0.1 GET /api/app/solve 200 uid=u42"
    assert mask_text(plain) == plain  # 无 PII 行不应被改动
    assert mask_text("保留中文题干：小明三题错两题") == "保留中文题干：小明三题错两题"


def test_mask_multiple_in_line():
    got = mask_text("a 13812345678 b 13998765432 c")
    assert "13812345678" not in got and "13998765432" not in got


def test_mask_text_non_string_passthrough():
    assert mask_text(7) == 7
    assert mask_text(None) is None


def test_mask_free_text_fields_nested():
    report = {
        "student_id": "student_003",  # 结构字段不脱敏
        "period_days": 7,
        "recent_events": [{"question": "爸爸手机号 13812345678 是多少？", "subject": "数学"}],
        "step_weakness": {"headline": "步骤薄弱", "top_steps": [{"step_label": "列方程", "count": 3}]},
        "suggestions": ["联系邮箱 teacher@xx.com 查看详情"],
    }
    out = mask_free_text_fields(report, ("question", "step_label", "headline", "suggestion", "subject"))
    assert out["student_id"] == "student_003"
    assert "138****5678" in out["recent_events"][0]["question"]
    assert out["recent_events"][0]["subject"] == "数学"  # 无 PII 不受影响
    assert "t*****r@xx.com" in out["suggestions"][0]
    assert out["step_weakness"]["top_steps"][0]["count"] == 3
