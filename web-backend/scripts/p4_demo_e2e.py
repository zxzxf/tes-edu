"""P4 第三方 demo 全链生产自检（容器内直连网关）。

链路：pth-token → 网关 /api/push/open/send → emit → PG push_message →
学生收件箱 /api/student/push/messages 可见 → 标已读 → 未读计数归零。

demonstrates：第三方拿 token 接入推送中心，平台学生 app 为第一收件端。
"""
import json
import urllib.request
import urllib.error

GW = "http://gateway:8080"
LOCAL = "http://127.0.0.1:8000"  # 容器内直连：学生身份用 X-User-* 头模拟（网关会强制 JWT）
PTH = "pth-708e52deb5d3c490702535b92aa2cd28"
RECEIVER = "p4demo-final-01"  # 测试学生 ID（网关注入身份用头模拟）


def call(method, path, body=None, headers=None, base=GW):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# 0) token-info 验明 token 有效
st, info = call("GET", "/api/push/open/token-info",
                headers={"Authorization": f"Bearer {PTH}"})
print("demo token-info:", st, info)
assert st == 200, "pth-token 失效，demo 无法继续"

# 1) 第三方 send（topic=system_test，走 app_offline 落收件箱）
st, sent = call("POST", "/api/push/open/send",
                body={"topic_code": "grade", "receiver": RECEIVER,
                      "title": "P4 第三方接入 demo",
                      "body": "这条消息由第三方 token 经 OpenAPI 发出，验证全链闭环。"},
                headers={"Authorization": f"Bearer {PTH}"})
print("demo send:", st, sent)
assert st == 200, "send 失败"
msg_id = sent["msg_id"]

# 2) 学生收件箱可见（模拟该学生身份；平台应用 owner_account_id IS NULL 才收）
STUDENT_H = {"X-User-Id": RECEIVER, "X-User-Role": "student", "X-Tenant-Id": "school_pilot_01"}
st, inbox = call("GET", "/api/student/push/messages", headers=STUDENT_H, base=LOCAL)
print("demo inbox:", st, "total=", inbox.get("total"), "unread=", inbox.get("unread_total"))
assert st == 200 and inbox["unread_total"] >= 1
assert any(m["msg_id"] == msg_id for m in inbox["items"]), "新消息未在收件箱"

# 3) 标已读 → 未读归零
st, _ = call("POST", f"/api/student/push/messages/{msg_id}/read", headers=STUDENT_H, base=LOCAL)
assert st == 200
st, inbox2 = call("GET", "/api/student/push/messages", headers=STUDENT_H, base=LOCAL)
print("demo after read: unread=", inbox2.get("unread_total"))
assert inbox2["unread_total"] == 0

# 4) 越权防护：另一个学生看不到这条消息
st, other = call("GET", "/api/student/push/messages", headers={
    "X-User-Id": "p4demo-final-02", "X-User-Role": "student", "X-Tenant-Id": "school_pilot_01"}, base=LOCAL)
assert st == 200 and other["total"] == 0, "越权：别人看到了 demo 消息"
print("demo 隔离验证: OK（他人 total=0）")
print("P4 THIRD-PARTY DEMO E2E: ALL PASS")
