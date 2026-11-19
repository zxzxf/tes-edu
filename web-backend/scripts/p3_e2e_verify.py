# P3 端到端验证：wechat_oa test send → 等分发 → 查 push_message 状态 + delivery_log 留痕
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
HDRS = {"Content-Type": "application/json", "X-User-Id": "admin",
        "X-User-Role": "admin", "X-Tenant-Id": "platform"}
PUSHPLUS_TOKEN = os.environ["PUSHPLUS_TOKEN"]


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=HDRS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


async def main():
    # 走渠道测试接口（内部 emit 一条 system_test 主题、显式 wechat_oa 渠道的消息）
    st, r = call("POST", "/api/admin/push/channels/wechat_oa/test",
                 {"channel_type": "wechat_oa", "receiver": PUSHPLUS_TOKEN})
    print("test-send:", st, r)
    if st != 200:
        sys.exit(1)
    msg_id = r["msg_id"]

    # Kafka 分发是异步的：轮询留痕最多 20s
    for _ in range(20):
        import asyncpg
        url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(url)
        msg = await conn.fetchrow(
            "SELECT status, title, topic_code FROM push_message WHERE msg_id=$1", msg_id)
        logs = await conn.fetch(
            "SELECT channel_type, result_code, detail, sent_at FROM push_delivery_log WHERE msg_id=$1",
            msg_id)
        await conn.close()
        if msg and msg["status"] in ("sent", "partial", "dead"):
            break
        await asyncio.sleep(1)
    print("message:", dict(msg) if msg else None)
    print("delivery_logs:", [dict(x) for x in logs])


asyncio.run(main())
