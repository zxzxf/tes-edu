"""通用 App 推送中心（Push Hub）服务包。

模块划分（对应 docx/通用App推送中心实施计划.md v1.4）：
- security    ：token 哈希/生成、portal 会话令牌（独立密钥+受众）、Fernet、bcrypt
- tokens      ：推送 token 生命周期（签发/校验/续期换发/吊销）
- routing     ：渠道路由优先级与订阅静音
- emit        ：单点写入口（PG 事实源 → Kafka → 降级就地分发）
- channels    ：渠道适配器（app_offline / wechat_oa；vendor/webhook 预留）
- portal_auth ：第三方账号注册/审批/角色门禁/数据墙
- ratelimit   ：应用/token/主题三级频控
"""
