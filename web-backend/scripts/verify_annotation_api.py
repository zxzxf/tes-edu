"""T-CAP1 审核 API 端到端验证（在 edu-web-backend 容器内直连 127.0.0.1:8000）。

登录拿 token → stats → 待校列表 → approve 一条 → reject 一条。
题库写回复核单独用 SQL 查（见验证输出末尾提示）。
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/admin"


def call(method, path, token=None, body=None, actor=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if actor:
        # 模拟网关 Header.Set 注入的身份头（容器内直连绕过网关，需手工带）
        headers.update({
            "X-User-Id": actor["user_id"],
            "X-User-Role": actor["role"],
            "X-Tenant-Id": actor.get("tenant_id", "school_pilot_01"),
        })
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


status, tok = call("POST", "/login", body={
    "username": "pilot@school01.edu", "password": "demo1234"})
print("LOGIN:", status, "role=", tok.get("role"), "token_len=", len(tok.get("access_token", "")))
if status != 200:
    raise SystemExit(0)
token = tok["access_token"]
# 审核 API 要 admin/teacher/region_admin；pilot 是 school 角色，故用 admin 身份头
admin = {"user_id": "admin", "role": "admin", "tenant_id": "platform"}

status, stats = call("GET", "/annotation/stats", token, actor=admin)
print("STATS:", status, json.dumps(stats, ensure_ascii=False)[:400])

status, items = call("GET", "/annotation/suggestions?status=pending&limit=10", token, actor=admin)
print("PENDING:", status, "count=", len(items) if isinstance(items, list) else items)
if not isinstance(items, list) or not items:
    raise SystemExit(0)

first = items[0]
iid, ch, field, val = first["id"], first["content_hash"], first["field_name"], first["suggested_value"]
print("APPROVE id=%s hash=%s %s=%s" % (iid, ch[:8], field, val))
status, res = call("PUT", "/annotation/suggestions/%s/review?decision=approved" % iid, token, actor=admin)
print("  APPROVE result:", status, res)

if len(items) > 1:
    second = items[1]
    print("REJECT id=%s hash=%s %s=%s" % (
        second["id"], second["content_hash"][:8], second["field_name"], second["suggested_value"]))
    status, res = call("PUT", "/annotation/suggestions/%s/review?decision=rejected" % second["id"], token, actor=admin)
    print("  REJECT result:", status, res)

# 非法值/重复审核防呆：再 approve 同一条应 400
status, res = call("PUT", "/annotation/suggestions/%s/review?decision=approved" % iid, token, actor=admin)
print("RE-APPROVE (expect 400):", status, str(res)[:120])

print("HASHES: approve=%s reject=%s" % (
    first["content_hash"][:8], items[1]["content_hash"][:8] if len(items) > 1 else "-"))
