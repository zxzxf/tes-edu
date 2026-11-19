# 通用 App 推送中心 · 实施计划（Push Hub）

> 版本：v1.3 ｜ 日期：2026-11-19 ｜ 状态：待评审
> 定位：把 student app 推送升级为**平台级通用离线推送服务**——一个自建 Kafka+Redis+PG 管道的推送中心，B 端管理员可视化配置推送渠道，第三方 App 通过 OpenAPI 接入使用，消息与接入凭证均支持有效期设置。
> **v1.0 渠道定稿：两种——① 微信公众号推送（`wechat_oa`，强触达）② App 离线推送（`app_offline`，收件箱不丢）**。
> **v1.2 增补：推送主题（`push_topic`）为一等公民——按主题分类推送，渠道路由/频控/默认有效期/订阅静音全部挂主题。**
> **v1.3 增补：第三方接入改为「推送 API + 推送 token」模型——token 有效期档位 1/3/6/12 个月，过期即拒、续期换发、随时吊销（取代原 HMAC 签名方案）。**
> 决策沿革：极光 JPush（弃）→ 纯站内轮询（v2.0，升级）→ 自建 Kafka+Redis（v3.0，扩展）→ 通用推送中心（v1.0）→ 两种渠道定稿（v1.1）→ 主题分类（v1.2）→ **推送 token 档位制（本版）**。原《student-app消息推送实施计划》并入本文档，student app 降为第一个接入端。

---

## 一、需求 → 设计映射

| 需求 | 设计 |
|---|---|
| 通用离线推送 | 消息一律先落 PG（`push_message`）再分发；接收端离线不丢，上线补收；Kafka 削峰解耦 |
| **按主题分类推送** | `push_topic` 主题注册制（发送必带 topic_code）：渠道路由、频控、默认 TTL、收件人订阅静音全部挂主题；App 收件箱按主题 Tab 分类、分主题未读 |
| 管理员可配置推送渠道 | B 端新增「推送中心」管理页：渠道 CRUD、凭证、开关、限频、测试发送；渠道为**插件式**，新渠道=新适配器 |
| 第三方 App 可接入 | 推送中心提供**推送 API + 推送 token**（管理页申请，Bearer 鉴权）；网关独立前缀 + 应用/token 双层频控 + IP 白名单可选 |
| 可设置有效期 | **四处有效期**：① 消息 TTL（`expire_at`，过期不再投递/不可查）② **推送 token 档位制有效期（1/3/6/12 个月，过期 401，续期=换发+7 天宽限）** ③ 渠道凭证有效期（到期页内提醒续配）④ 应用禁用联动 token 即时失效 |

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
（Bearer token+档位有效期）│   鉴权: push_token(hash, expired_at)     ├─▶ wechat_oa 适配器 ──▶ PushPlus /send（渠道 token 管理员配/有效期）
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
push_app          接入应用：name, allowed_channels jsonb, rate_limit_per_min,
                  ip_allow, enabled, created_by（应用是主体，凭证独立成 token 表）
push_token        推送 token：app_id, token_hash(SHA-256 unique), token_prefix(展示用 pth-ab12),
                  tier(1/3/6/12 个月), issued_at, expired_at, grace_until(续期宽限),
                  status(active/rotated/revoked), rate_limit_per_min(可选收紧), last_used_at
push_topic        推送主题：topic_code(应用内 unique), app_id, name, 示例 homework/grade/
                  wrongbook/system/notice；default_channels jsonb(主题级渠道路由),
                  default_ttl, rate_limit_per_day, force_channels jsonb, enabled
                  ——**渠道路由/频控/默认有效期挂主题**
push_channel      渠道配置：channel_type, name, config jsonb(凭证密文 Fernet),
                  cred_valid_until, enabled, rate_limit, owner_scope(admin 配/应用可选)
push_subscription 收件人订阅：app_id, receiver, topic_code, muted bool, channel_override
                  jsonb(可选，个人改此主题的触达渠道)——App 端"消息设置"开关的数据源
push_message      消息主体：msg_id(uuid unique,幂等), app_id, **token_id**(留痕谁发的),
                  **topic_code**, receiver(用户ID/alias/openid),
                  title, body, payload jsonb, channel_types jsonb(空=按主题路由→再退应用默认),
                  expire_at, status(pending/sent/partial/expired/dead), created_at；
                  索引 (receiver, topic_code, created_at desc)
push_delivery_log 投递留痕：msg_id, channel_type, attempt, result_code, detail, sent_at
push_unread       不落表——Redis notify:unread:{app}:{uid}（+ 按主题 notify:unread:{app}:{uid}:{topic}）
                  + ZSet 收件箱热缓存
```

**主题模型要点**（v1.2 新增）：
- **主题是一等公民**：发送必带 `topic_code`（应用内注册制，未注册主题拒发 422），杜绝第三方乱发。
- **渠道路由优先级**：消息显式 `channel_types` > 主题 `default_channels` > 应用默认。典型：`grade` 主题=微信+App双通道引流，`notice` 主题=仅 App 离线。
- **订阅与静音**：收件人可按主题静音（muted 则 app_offline 不计数但仍落库可查）；`wechat_oa` 是否跟订阅走取决于配置（运营公告类可强制微信触达，主题上打标 `force_channels`）。
- **分类消费**：App 收件箱顶部主题 Tab（全部/作业/成绩/错题/公告），未读角标支持总数+分主题。
- **频控按主题**：`rate_limit_per_day` 防某主题刷屏（尤其微信渠道日 200 次额度要省着花）。

**有效期语义（四类，v1.3 更新）**：
- 消息：`expire_at = created_at + ttl`（ttl 优先级：调用方传 > 主题 default_ttl > 应用默认；过期消息 dispatcher 跳过并标 `expired`，收件箱查询过滤）
- **推送 token：档位制 1/3/6/12 个月**——过期 send 全 401 `TOKEN_EXPIRED`；续期=换发新 token（旧 token 7 天宽限并行）；可随时吊销；Redis 缓存 `push:tok:{hash}` TTL 对齐 expired_at 实现免扫表过期
- 应用：`enabled=false` 联动其全部 token 即时失效（401 TOKEN_INVALID）
- 渠道：`cred_valid_until` 到期 → 管理页提醒，不影响其他渠道（渠道级降级）

## 五、OpenAPI（第三方接入）——推送 API + 推送 token

**接入模型（v1.3 定稿）**：第三方在管理页注册应用 → 申请**推送 token**（选有效期档位）→ 拿 token 直接调推送 API。**不再使用 HMAC 签名方案**（app_key/timestamp/nonce/sign 全部移除，链路更简单；https 传输 + Bearer token 即满足内部平台间调用安全）。

```
鉴权：Authorization: Bearer <push_token>
      token 格式 pth-<32位随机>；PG 存 SHA-256 哈希，明文仅签发时显示一次

POST /api/push/open/send
Header: Authorization: Bearer pth-xxxx
Body:   { topic_code, receiver, title, body, payload?, channel_types?, ttl_seconds? }
Resp:   200 { msg_id, status }
        401 { code: TOKEN_INVALID | TOKEN_EXPIRED }   ← token 无效/过期，过期信息含 expired_at
        422 未注册主题    429 超 token QPS/应用频控
辅助接口（同 Bearer 鉴权）：
GET  /api/push/open/topics        本应用已注册主题列表
GET  /api/push/open/token-info    当前 token 的档位/剩余有效期（接入方自查）
```

**推送 token 有效期档位（管理页申请时四选一，不支持永不过期）**：

| 档位 | 适用 |
|---|---|
| **1 个月** | 联调试用、临时活动 |
| **3 个月** | 短周期项目 |
| **6 个月** | 常规接入默认档 |
| **12 个月** | 长期稳定接入 |

token 生命周期规则：
- **过期即拒**：到期后 send 全 401 `TOKEN_EXPIRED`，网关/中间件层校验（PG+Redis 缓存 `push:tok:{hash}`，TTL 到 expired_at 自动失效，天然免改码过期）。
- **续期 = 换发**：不提供原地延期；管理页"续期"按钮按所选新档位签发**新 token**，旧 token 保留 `grace_period=7 天` 过渡并行（防接入方换值窗口期断流），7 天后旧 token 立即失效。
- **随时吊销**：管理页一键吊销立即 401，重发即可；应用禁用联动全部 token 失效。
- **限额挂 token**：每 token 独立 QPS/日配额（默认沿用应用级，可在 token 上单独收紧），防止一个接入方打爆微信日额度。

- 网关 `gateway/config.yaml` 新增 `/api/push/open/` 前缀：独立鉴权（非 RBAC，走 push_token），限流按应用+token 双层。
- 管理接口 `/api/admin/push/*` 走既有 rbac + 强制租户，仅 admin。
- 平台内业务（作业/判分）不走 HTTP 自环，直接调 `app/services/push_hub/emit.py`（同参数同语义，内部生成 msg_id 后同一 Kafka 管道）——**内外一条路，杜绝双实现漂移**。

## 六、B 端管理页（挂 RBAC 菜单，仅 admin）

| 页面 | 功能 |
|---|---|
| 推送总览 | 今日发送量/各渠道成功率/失败 Top、**按主题维度统计**、Kafka lag、**临期 token 提醒区** |
| 接入应用 | push_app CRUD（名称/allowed_channels/应用频控/禁用）→ **推送 token 签发弹窗：选 1/3/6/12 个月档位，明文仅显示一次**；token 列表（档位、签发/到期、剩余天数、QPS 配额、**续期=换发**、吊销） |
| **推送主题** | push_topic CRUD（应用内注册制）、主题级渠道路由/默认 TTL/日频控/force_channels 标记 |
| 渠道管理 | 渠道 CRUD、凭证配置（密文存储不回显）、有效期提醒、**单渠道测试发送** |
| 消息查询 | push_message 分页（按应用/**主题**/接收人/状态/时间）、重发死信、手动过期 |

前端三要素按项目铁律执行：`router/index.js` + `menu.js MENU_ROUTE_MAP` + `rbac_seed.py MENUS/ROLE_MENU_MAP`，缺任一项必被 `hasRouteAccess` 弹回。

## 七、student app（第一接入端）改造

- 注册为 push_app，预置 5 个主题：`homework`（作业下发）、`grade`（判分完成，微信+App 双通道）、`wrongbook`（错题复盘）、`system`（系统公告）、`practice`（练习提醒，预留）。
- 消息中心页 `MessagesScreen.kt`：**顶部主题 Tab**（全部/作业/成绩/错题/公告）+ 底部「我的」未读 Badge（总数，展开看分主题）+ 前台 25s 长轮询（OkHttp readTimeout 设 30s，客户端超时必须 > 服务端挂起）。
- 消息设置页：按主题静音开关（写 push_subscription）。
- 判分完成/作业下发业务钩子调内部 emit 带对应 topic_code（替代 v3.0 计划的 student_message 表——被 push_message 通用模型取代）。

## 八、安全与风险

- **token 只存哈希**（SHA-256，明文仅签发时显示一次）、渠道凭证 Fernet 密文（密钥走环境变量），日志脱敏；PushPlus token/极光 master secret 不进库不进 git。
- **token 滥用防线**：https 传输 + 应用级频控 + token 级 QPS/日配额 + 档位有效期自然淘汰 + 一键吊销 + 禁用联动；last_used_at 便于发现僵尸 token 定期收编。
- **幂等**：msg_id unique + Kafka at-least-one，dispatcher 重复消费只投一次；测试必锁。
- **Redis 是加速层**：所有计数/缓存可自 PG 重建；照 `cache.py` 惯例静默降级，禁止 Redis 挂 → 接口 500。
- **CF 30s**：长轮询上限 25s，测试断言响应 <30s。
- **推送内容脱敏**：title/body 不放大额/成绩数值，详情点开走接口。
- **内存**：dispatch_worker 随主进程，heap 增量≈0；Kafka 复用现有集群不新建。
- 合规：微信渠道沿用 PushPlus 实名前提（一次性收费、终生有效）；第三方接入的推送内容由接入方自负，平台留 push_delivery_log 可溯源。

## 九、分期与工作量

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P1 推送中心内核 | 7 张表（含 **push_token/push_topic/push_subscription**）+ **推送 token 签发/校验中间件（档位 1/3/6/12 月、Redis TTL 过期、吊销联动）** + emit/OpenAPI 带 topic_code(Kafka+降级+幂等+主题注册校验) + dispatch_worker（**主题渠道路由+静音+主题频控**）+ **app_offline 渠道**（收件箱/**主题 Tab 接口/分主题未读**/长轮询）+ 四类有效期 | 4–5 天 |
| P2 管理端 | 5 个 B 端页（含主题管理 + **token 签发弹窗/续期换发/吊销/临期提醒**）+ RBAC 三件套 + 渠道凭证加密 + 测试发送 | 2–3 天 |
| P3 微信渠道 | **wechat_oa 适配器**（`WechatOAProvider` 抽象 + PushPlus 实现，token 管理员配置化+有效期）+ vendor/webhook 适配器接口占位 | 1–2 天 |
| P4 接入验证 | student app 消息中心（主题 Tab+静音设置）改造 + 一个第三方 demo 走通 **token+主题接入** + 发版冒烟 | 2–3 天 |
| 合计 | | **9–13 天** |

> 依赖顺序：P1 → P2/P3 并行 → P4。股票盯盘计划里的 PushPlus 直调改为消费推送中心的 wechat 渠道（避免两处各接一套）。

## 十、验收清单

- [ ] 第三方拿**推送 token**（Bearer）send 一条（带 topic_code）→ 接收端 App 下次打开**对应主题 Tab** 收件箱内可见（离线不丢）
- [ ] token 生命周期全链路：签发选 1/3/6/12 月档→明文只显示一次→过期 send 401 `TOKEN_EXPIRED`→续期换发旧 token 7 天宽限并行→吊销即时 401→禁用应用联动全 token 失效
- [ ] 主题路由生效：`grade` 主题双通道（微信引流+App 全量）、未注册主题拒发 422、主题日频控超限只丢不炸
- [ ] 收件人对主题静音后：app_offline 不计数但落库可查；force_channels 主题微信仍触达
- [ ] `wechat_oa` 渠道：管理员配好 PushPlus token 后测试发送，微信秒级收到；带微信的判分消息"引流文案+App 完整消息"双通道都落留痕
- [ ] 前台长轮询 25s 内收到、角标正确；Redis FLUSH 后计数回源无感
- [ ] 重复投递同 msg_id 只发一次；过期消息（expire_at）不再投递且收件箱不可见
- [ ] 管理页：建应用/签发 token/配两种渠道/改主题/禁用/测试发送全闭环；token 与渠道凭证均密文/哈希存储不回显
- [ ] Kafka 停用时 emit 降级直写、消息不丢；接口无 500
- [ ] 前端单测 + 后端全量通过；发版 137 冒烟通过，回滚点已打

---

*本计划基于 2026-11-19 代码库实测（Kafka/Redis/worker/cache 惯例/网关前缀鉴权均为现成件）。《student-app消息推送实施计划》v3.0 并入本文档，student app 为第一接入端。*
