"""诊断：最近 wechat_oa 投递留痕 + 渠道配置状态。"""
import asyncio
import asyncpg
import os
import json
import urllib.request


async def main():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(url)

    rows = await c.fetch(
        "SELECT m.msg_id, m.receiver, m.status, m.title, m.created_at, "
        "l.result_code, l.detail "
        "FROM push_message m LEFT JOIN push_delivery_log l ON l.msg_id = m.msg_id "
        "WHERE l.channel_type = 'wechat_oa' ORDER BY m.created_at DESC LIMIT 10"
    )
    print("=== 最近 wechat_oa 投递 ===")
    for r in rows:
        d = dict(r)
        d["created_at"] = str(d["created_at"])
        print(json.dumps(d, ensure_ascii=False, default=str))

    ch = await c.fetchrow(
        "SELECT enabled, config FROM push_channel WHERE channel_type='wechat_oa'"
    )
    print("=== 渠道行 ===")
    if ch is None:
        print("wechat_oa 渠道行不存在！")
    else:
        cfg = ch["config"] or {}
        print("enabled:", ch["enabled"], "config keys:", list(cfg.keys()))
        # 解密看 token 前后缀（不打印明文）
        from app.services.push_hub import security
        try:
            plain = json.loads(security.dec_channel_config(cfg.get("enc", "")))
            tk = plain.get("pushplus_token", "")
            print("pushplus_token:", tk[:6] + "..." + tk[-4:] if tk else "EMPTY", "len=", len(tk))
            print("other cfg:", {k: v for k, v in plain.items() if k != "pushplus_token"})
        except Exception as e:
            print("解密失败:", e)

    # 渠道 token 直连 PushPlus 实测一发（推渠道主自己）
    if ch and ch["enabled"]:
        try:
            plain = json.loads(security.dec_channel_config(cfg.get("enc", "")))
            tk = plain.get("pushplus_token", "")
            req = urllib.request.Request(
                "https://www.pushplus.plus/send",
                data=json.dumps({
                    "token": tk, "title": "渠道诊断直发",
                    "content": "11-20 诊断：渠道 token 直发渠道主（应到你微信）",
                    "channel": "wechat",
                }).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            resp = json.loads(urllib.request.urlopen(req, timeout=12).read())
            print("=== 直连实测 ===", resp)
        except Exception as e:
            print("直连实测失败:", e)

    await c.close()


asyncio.run(main())
