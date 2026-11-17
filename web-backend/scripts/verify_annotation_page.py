import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/api/admin"
HEADERS = {
    "Content-Type": "application/json",
    "X-User-Id": "u_admin",
    "X-User-Role": "admin",
    "X-Tenant-Id": "platform",
}


def call(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


status, body = call("/menu/my")
menu = json.loads(body) if status == 200 else []
flat = [m.get("path") for m in (menu if isinstance(menu, list) else [])]
print("menu/my:", status, "nodes:", len(flat))
print("  has /ops/annotation:", "/ops/annotation" in flat)
print("  has /ops/compliance:", "/ops/compliance" in flat)

status, body = call("/annotation/stats")
print("stats:", status, body[:230])
status, body = call("/annotation/suggestions?status=pending&limit=3")
print("suggestions:", status, body[:230])

school_headers = dict(HEADERS)
school_headers["X-User-Role"] = "school"
school_headers["X-User-Id"] = "u_school"
school_headers["X-Tenant-Id"] = "demo"
status, body = call("/annotation/stats", school_headers)
print("school stats:", status, "(expect 403)")
