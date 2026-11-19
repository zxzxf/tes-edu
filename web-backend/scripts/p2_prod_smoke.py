# P2 发版后生产自检：admin 只读接口 + portal 链路（容器内直连，注入头鉴权）
import json
import urllib.error
import urllib.request

B = "http://127.0.0.1:8000"
H = {"X-User-Id": "admin", "X-User-Role": "admin", "X-Tenant-Id": "platform"}


def call(method, path, body=None):
    req = urllib.request.Request(
        B + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={**H, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()[:220]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


for m, p in [
    ("GET", "/api/admin/push/apps"),
    ("GET", "/api/admin/push/stats?days=7"),
    ("GET", "/api/admin/push/messages?page=1&size=3"),
    ("GET", "/api/admin/push/channels"),
    ("GET", "/api/admin/push/apps/1/topics"),
    ("GET", "/api/admin/push/apps/1/tokens"),
]:
    print(m, p, "->", *call(m, p))

# SELF 占位 test send（走渠道→PushPlus 真发一条，验证 P2 页的测试发送按钮链路）
st, r = call("POST", "/api/admin/push/channels/wechat_oa/test",
             {"channel_type": "wechat_oa", "receiver": "SELF"})
print("TEST-SEND SELF ->", st, r)
