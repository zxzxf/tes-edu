# P2 发版网关自检：经 gateway:8080 验证三条链路
# 1) /api/push/open/token-info 带 pth- → 200（证明新前缀代理到 web-backend 而非 / fallback 的 agent）
# 2) /api/push/portal/register 无鉴权可达（mode:"" 透传；重复邮箱 400/409 也算链路通）
# 3) /api/admin/push/stats 带主站登录态 → 200（rbac 路由不受影响）
import json
import urllib.error
import urllib.request

GW = "http://gateway:8080"
PTH = "pth-708e52deb5d3c490702535b92aa2cd28"


def call(method, path, body=None, headers=None, big=None):
    req = urllib.request.Request(
        GW + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = r.read().decode()
            return r.status, (d[:200] if big is None else d)
    except urllib.error.HTTPError as e:
        d = e.read().decode()
        return e.code, (d[:200] if big is None else d)


# 1) pth- token-info 经网关
st, r = call("GET", "/api/push/open/token-info", headers={"Authorization": f"Bearer {PTH}"})
print("token-info:", st, r)

# 1b) 无 token 期望 401
st, r = call("GET", "/api/push/open/token-info")
print("token-info-noauth:", st, r)

# 2) portal register 经网关（用测试邮箱，重复注册应 400/409，均代表链路通）
st, r = call("POST", "/api/push/portal/register", {
    "email": "p2smoke@verify.local", "password": "Sm0ke!pass", "org_name": "P2自检"})
print("portal-register:", st, r)

# 3) 主站 admin 链路回归：登录 → /api/admin/push/stats
#    （JWT 500+ 字符，此步响应不截断，否则 json 解析被截爆）
st, r = call("POST", "/login", {"username": "admin@tes.edu", "password": "demo1234"}, big=0)
tok = json.loads(r)["access_token"] if st == 200 else ""
print("login:", st)
st, r = call("GET", "/api/admin/push/stats?days=1", headers={"Authorization": f"Bearer {tok}"})
print("admin-push-stats:", st, r)
