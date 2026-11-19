# P3 配置脚本：容器内直连 web-backend，走 /api/admin/push 完整 HTTP 链
# 步骤：建平台应用 → 配 wechat_oa 渠道（Fernet 加密）→ 建 grade 双通道主题
#      → 签发 pth-token(1月档)。PushPlus 渠道 token 从环境变量读取。
import json
import urllib.error
import urllib.request

import os

BASE = "http://127.0.0.1:8000"
HDRS = {
    "Content-Type": "application/json",
    "X-User-Id": "admin",
    "X-User-Role": "admin",
    "X-Tenant-Id": "platform",
}
PUSHPLUS_TOKEN = os.environ["PUSHPLUS_TOKEN"]


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=HDRS,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# 1) 平台自有应用（双渠道）
st, app = call("POST", "/api/admin/push/apps", {
    "name": "平台验证应用(P3)",
    "allowed_channels": ["app_offline", "wechat_oa"],
    "rate_limit_per_min": 60,
})
print("app:", st, app)
app_id = app["app_id"]

# 2) wechat_oa 渠道（凭证只进不出）
st, ch = call("PUT", "/api/admin/push/channels/wechat_oa", {
    "channel_type": "wechat_oa",
    "name": "微信公众号(PushPlus免费渠道)",
    "enabled": True,
    "rate_limit_per_min": 30,
    "credentials": {"pushplus_token": PUSHPLUS_TOKEN},
})
print("channel:", st, ch)

# 3) 渠道状态回查（确认 has_credentials=True 且不回显明文）
st, rows = call("GET", "/api/admin/push/channels")
print("channels-list:", st, json.dumps(rows, ensure_ascii=False))

# 4) grade 主题：微信+App 双通道（计划书典型场景）
st, tp = call("POST", f"/api/admin/push/apps/{app_id}/topics", {
    "topic_code": "grade",
    "name": "成绩通知",
    "default_channels": ["wechat_oa", "app_offline"],
    "rate_limit_per_day": 500,
})
print("topic:", st, tp)

# 5) 签发 1 个月档 pth-token
st, tk = call("POST", f"/api/admin/push/apps/{app_id}/tokens", {"tier": 1})
print("token:", st, tk)
