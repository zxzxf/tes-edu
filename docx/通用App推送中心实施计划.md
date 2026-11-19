# 通用 App 推送中心 · 实施计划（Push Hub）

> 版本：v1.1 ｜ 日期：2026-11-19 ｜ 状态：待评审
> 定位：把 student app 推送升级为**平台级通用离线推送服务**——一个自建 Kafka+Redis+PG 管道的推送中心，B 端管理员可视化配置推送渠道，第三方 App 通过 OpenAPI 接入使用，消息与接入凭证均支持有效期设置。
> **v1.0 渠道定稿：两种——① 微信公众号推送（`wechat_oa`，强触达）② App 离线推送（`app_offline`，收件箱不丢）**。
> 决策沿革：极光 JPush（弃）→ 纯站内轮询（v2.0，升级）→ 自建 Kafka+Redis（v3.0，扩展）→ 通用推送中心（v1.0）→ **两种渠道定稿（本版）**。原《student-app消息推送实施计划》并入本文档，student app 降为第一个接入端。

---

## 一、需求 → 设计映射

| 需求 | 设计 |
|---|---|
| 通用离线推送 | 消息一律先落 PG（`push_message`）再分发；接收端离线不丢，上线补收；Kafka 削峰解耦 |
| 管理员可配置推送渠道 | B 端新增「推送中心」管理页：渠道 CRUD、凭证、开关、限频、测试发送；渠道为**插件式**，新渠道=新适配器 |
| 第三方 App 可接入 | OpenAPI（注册应用 → app_key/secret → 签名调用 send 接口）；网关独立前缀 + 频控 + IP 白名单可选 |
| 可设置有效期 | **三处有效期**：① 消息 TTL（`expire_at`，过期不再投递/不可查）② 接入应用授权有效期（`valid_until`，过期 401）③ 渠道凭证有效期（到期页内提醒续配） |

## 二、渠道体系：v1.0 两种渠道（决策定稿）

v1.0 推送中心只提供**两种渠道**，各管一头触达：

| channel_type | 名称 | 触达位置 | 到达时效 | 技术路线 | 前置 |
|---|---|---|---|---|---|
| `wechat_oa` | **微信公众号推送** | 微信服务号消息（人不打开任何 App 也能看到） | 秒~分钟级 | PushPlus 微信公众号免费渠道（token 渠道凭证管理员配置；实名一次性）；抽象为 `WechatOAProvider`，将来客户有自持认证服务号可换实现 | 管理员完成 PushPlus 实名并配 token |
| `app_offline` | **App 离线推送** | 接入 App 的站内收件箱 + 未读角标 | 打开 App 即补收；前台 25s 长轮询准实时 | PG push_message 持久化（离线不丢的事实源）→ Redis 未读/ZSet → App 拉取/长轮询 | 接入方 App 集成收件箱 SDK（本平台先做 student app 示范） |

两种渠道的分工与互补：

```
微信推送 = 人找消息 → 强触达，离开 App 也能到，但只有一句话标题+摘要
App 离线推送 = 消息找人 → 全量可靠，消息中心/详情/状态（已读）全在 App 内闭环
典型用法：判分完成 → 微信推一条"成绩已出，点击查看"引流 + App 收件箱落完整消息
```

- **架构预留（v1.0 不做）**：`vendor`（极光/厂商通道，App 被杀弹系统通知栏）、`webhook`（回调第三方服务器）只保留适配器接口位，管理员配不了、文档不承诺。
- 固有边界说明：`app_offline` 的名字指"离线期间不丢、上线必达"，**不含**系统通知栏弹窗（那是 vendor 位的能力）；v1.0 的离线强触达由 `wechat_oa` 承担。

## 三、架构

```
                 ┌──────────────────────── 推送中心（web-backend 内 push_hub 子模块） ────────────────────────┐
业务方(平台内) ──┐                                            ┌─▶ app_offline 适配器 ─▶ Redis 未读/ZSet + App 拉取/长轮询
第三方 App ─────┼▶ OpenAPI /api/push/open/send               │
（签名+有效期）  │   鉴权: push_app(app_key,sign,valid_until)  ├─▶ wechat_oa 适配器 ──▶ PushPlus /send（渠道 token 管理员配/有效期）
                │   → 写 push_message(expire_at) → Kafka      │
B 端管理页 ─────┘        push_dispatch topic                   └─▶ [vendor/webhook 预留适配器，v1.0 不实现]
（渠道/应用/消息管理）            │
                          dispatch_worker（幂等 by msg_id，渠道路由 + 频控 + 投递留痕）
                                  │
                                  └─▶ push_delivery_log（每渠道每次投递结果）
PG = 唯一事实源（离线不丢、分页补收、状态）  Redis = 计数/热缓存（可重建）  Kafka = 削峰（不可用降级直发）
```

- 单写者原则：推送写链路统一归 web-backend；agent-backend/第三方都只调 OpenAPI。
- worker 随 web-backend lifespan 起（137 available 仅 1.8G，不新建容器）。

## 四、数据模型（migrations/00NN_push_hub.sql）

```
push_app          接入应用：name, app_key(unique), secret_hash, allowed_channels jsonb,
                  rate_limit_per_min, ip_allow, valid_until, enabled, created_by
push_channel      渠道配置：channel_type, name, config jsonb(凭证密文 Fernet),
                  cred_valid_until, enabled, rate_limit, owner_scope(admin 配/应用可选)
push_message      消息主体：msg_id(uuid unique,幂等), app_id, receiver(用户ID/alias/openid),
                  title, body, payload jsonb, channel_types jsonb(空=应用默认渠道),
                  expire_at, status(pending/sent/partial/expired/dead), created_at
push_delivery_log 投递留痕：msg_id, channel_type, attempt, result_code, detail, sent_at
push_unread       不落表——Redis notify:unread:{app}:{uid} + ZSet 收件箱热缓存
```

**有效期语义**：
- 消息：`expire_at = created_at + ttl`（ttl 由调用方传或应用默认，单位秒；过期消息 dispatcher 跳过并标 `expired`，收件箱查询过滤）
- 应用：`valid_until` 过期 → OpenAPI 全部 401，页面标红"授权已过期"
- 渠道：`cred_valid_until` 到期 → 管理页提醒，不影响其他渠道（渠道级降级）

## 五、OpenAPI（第三方接入）

```
POST /api/push/open/send
Header: X-App-Key / X-Timestamp / X-Nonce / X-Sign
        sign = HMAC-SHA256(secret, app_key+timestamp+nonce+body_sha256)  ±300s 防重放
Body:   { receiver, title, body, payload?, channel_types?, ttl_seconds? }
Resp:   { msg_id, status }
```
- 网关 `gateway/config.yaml` 新增 `/api/push/open/` 前缀：独立鉴权（非 RBAC，走 app_key），限流按 `push_app.rate_limit_per_min`。
- 管理接口 `/api/admin/push/*` 走既有 rbac + 强制租户，仅 admin。
- 平台内业务（作业/判分）不走 HTTP 自环，直接调 `app/services/push_hub/emit.py`（同参数同语义，内部生成 msg_id 后同一 Kafka 管道）——**内外一条路，杜绝双实现漂移**。

## 六、B 端管理页（挂 RBAC 菜单，仅 admin）

| 页面 | 功能 |
|---|---|
| 推送总览 | 今日发送量/各渠道成功率/失败 Top、Kafka lag |
| 接入应用 | push_app CRUD、生成密钥对（secret 仅创建时明文显示一次）、有效期、频控、禁用 |
| 渠道管理 | 渠道 CRUD、凭证配置（密文存储不回显）、有效期提醒、**单渠道测试发送** |
| 消息查询 | push_message 分页（按应用/接收人/状态/时间）、重发死信、手动过期 |

前端三要素按项目铁律执行：`router/index.js` + `menu.js MENU_ROUTE_MAP` + `rbac_seed.py MENUS/ROLE_MENU_MAP`，缺任一项必被 `hasRouteAccess` 弹回。

## 七、student app（第一接入端）改造

- 注册为 push_app（默认渠道 `app_offline`，可选叠加 `wechat_oa`）。
- 消息中心页 `MessagesScreen.kt` + 底部「我的」未读 Badge + 前台 25s 长轮询（OkHttp readTimeout 设 30s，客户端超时必须 > 服务端挂起）。
- 判分完成/作业下发业务钩子调内部 emit（替代 v3.0 计划的 student_message 表——被 push_message 通用模型取代）。

## 八、安全与风险

- **secret 只存哈希**、渠道凭证 Fernet 密文（密钥走环境变量），日志脱敏；PushPlus token/极光 master secret 不进库不进 git。
- **签名防重放**（timestamp±300s + nonce 去重 Redis）；第三方滥用防线：应用级频控 + 日配额 + 一键禁用。
- **幂等**：msg_id unique + Kafka at-least-one，dispatcher 重复消费只投一次；测试必锁。
- **Redis 是加速层**：所有计数/缓存可自 PG 重建；照 `cache.py` 惯例静默降级，禁止 Redis 挂 → 接口 500。
- **CF 30s**：长轮询上限 25s，测试断言响应 <30s。
- **推送内容脱敏**：title/body 不放大额/成绩数值，详情点开走接口。
- **内存**：dispatch_worker 随主进程，heap 增量≈0；Kafka 复用现有集群不新建。
- 合规：微信渠道沿用 PushPlus 实名前提（一次性收费、终生有效）；第三方接入的推送内容由接入方自负，平台留 push_delivery_log 可溯源。

## 九、分期与工作量

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P1 推送中心内核 | 4 张表 + emit/OpenAPI(Kafka+降级+幂等) + dispatch_worker + **app_offline 渠道**（收件箱/未读/长轮询接口）+ 消息有效期 | 3–4 天 |
| P2 管理端 | 4 个 B 端页 + RBAC 三件套 + 渠道凭证加密 + 测试发送 | 2–3 天 |
| P3 微信渠道 | **wechat_oa 适配器**（`WechatOAProvider` 抽象 + PushPlus 实现，token 管理员配置化+有效期）+ vendor/webhook 适配器接口占位 | 1–2 天 |
| P4 接入验证 | student app 消息中心改造 + 一个第三方 demo 走通签名接入 + 发版冒烟 | 2–3 天 |
| 合计 | | **8–12 天** |

> 依赖顺序：P1 → P2/P3 并行 → P4。股票盯盘计划里的 PushPlus 直调改为消费推送中心的 wechat 渠道（避免两处各接一套）。

## 十、验收清单

- [ ] 第三方用 app_key 签名 send 一条 → 接收端 App 下次打开收件箱内可见（离线不丢）
- [ ] `wechat_oa` 渠道：管理员配好 PushPlus token 后测试发送，微信秒级收到；带微信的判分消息"引流文案+App 完整消息"双通道都落留痕
- [ ] 前台长轮询 25s 内收到、角标正确；Redis FLUSH 后计数回源无感
- [ ] 重复投递同 msg_id 只发一次；过期消息（expire_at）不再投递且收件箱不可见
- [ ] 管理页：建应用/配两种渠道/改有效期/禁用/测试发送全闭环；渠道凭证密文存储不回显
- [ ] valid_until 过期后 OpenAPI 全 401；渠道过期只降级该渠道不影响他渠道
- [ ] Kafka 停用时 emit 降级直写、消息不丢；接口无 500
- [ ] 前端单测 + 后端全量通过；发版 137 冒烟通过，回滚点已打

---

*本计划基于 2026-11-19 代码库实测（Kafka/Redis/worker/cache 惯例/网关前缀鉴权均为现成件）。《student-app消息推送实施计划》v3.0 并入本文档，student app 为第一接入端。*
