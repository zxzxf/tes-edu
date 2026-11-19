# 通用 App 推送中心 · 实施计划（Push Hub）

> 版本：v1.6 ｜ 日期：2026-11-19 ｜ 状态：**✅ 四阶段全部完成并上线 137（P1/P2/P3/P4 闭环）**
> 定位：把 student app 推送升级为**平台级通用离线推送服务**——一个自建 Kafka+Redis+PG 管道的推送中心，B 端管理员可视化配置推送渠道，第三方 App 通过 OpenAPI 接入使用，消息与接入凭证均支持有效期设置。
> **v1.0 渠道定稿：两种——① 微信公众号推送（`wechat_oa`，强触达）② App 离线推送（`app_offline`，收件箱不丢）**。
> **v1.2 增补：推送主题（`push_topic`）为一等公民——按主题分类推送，渠道路由/频控/默认有效期/订阅静音全部挂主题。**
> **v1.3 增补：第三方接入改为「推送 API + 推送 token」模型——token 有效期档位 1/3/6/12 个月，过期即拒、续期换发、随时吊销（取代原 HMAC 签名方案）。**
> **v1.4 增补：第三方自助注册推送中心账号（push_account，admin 审批制）+ 三档角色（developer/operator/viewer）；第三方权限闭包只限推送中心域内接口，与主站 RBAC 身份域硬隔离。**
> **v1.5 实施进展（同日）：P1 内核（commit `3e74ea3`，12 单测）、P3 微信渠道（端到端实发验证 `sent/ok`；修复 receiver 语义——SELF/本人=一对一，32 位他人令牌=好友 `to` 转发）、P2 管理端（主站 6 页 + portal 门户，commit `20e804c`）、网关 `/api/push/` 前缀路由（`0ff75e0`）均已完成并**发布 137 生产**（回滚点 bak-20261119）。新增生产资产：平台应用#1、wechat_oa 渠道、grade/system_test 主题、pth-token（12-19 到期）。已知平台边界：PushPlus 要求接收方本人实名且互加好友，否则 `905/999`——消息标 dead 可在消息查询页「重发」。剩 P4：student app 消息中心改造 + 第三方 demo 全链验证。**
> **v1.6 收官（同日）：P4 全链闭环——student app 消息中心（主题 Tab + 未读角标 + 25s 长轮询 + 已读 + payload 跳转，APK 16M 编译通过，commit 首笔）；判分完成/作业下发业务钩子（`business.py`，独立会话 fire-and-forget + 幂等 msg_id + 总开关 `push_business_notify_enabled`，生产实测 grade 主题落库 `partial`、重复触发 `row count=1`）；第三方 demo 全链生产 ALL PASS（token-info→send→收件箱 unread=1→已读归零→他人隔离 total=0）。过程中修两个真 bug：① PushApi 漏 `api/student/` 前缀会落 `/` fallback 打到 agent_backend；② 新消息只 INCR per-app 未读键、没失效 agg 键导致收件箱未读停在旧值（生产脏读实证，清键前 unread=0/清后=2）——已统一 `unread_agg_key()` 并在 `bump_inbox_counters` 失效它。后端全量 979 passed（唯一失败为既有无关 `test_course_kp_api`），推送专测 17 项全绿。提交 `f3b0f7e`（钩子+缓存修复）+ `e552fc1`（幂等日志优化），均已上线 137（回滚点 bak-20261119）。**
> 决策沿革：极光 JPush（弃）→ 纯站内轮询（v2.0，升级）→ 自建 Kafka+Redis（v3.0，扩展）→ 通用推送中心（v1.0）→ 两种渠道定稿（v1.1）→ 主题分类（v1.2）→ 推送 token 档位制（v1.3）→ **第三方自助账号+角色体系（本版）**。原《student-app消息推送实施计划》并入本文档，student app 降为第一个接入端。

---

## 一、需求 → 设计映射

| 需求 | 设计 |
|---|---|
| 通用离线推送 | 消息一律先落 PG（`push_message`）再分发；接收端离线不丢，上线补收；Kafka 削峰解耦 |
| **按主题分类推送** | `push_topic` 主题注册制（发送必带 topic_code）：渠道路由、频控、默认 TTL、收件人订阅静音全部挂主题；App 收件箱按主题 Tab 分类、分主题未读 |
| 管理员可配置推送渠道 | B 端新增「推送中心」管理页：渠道 CRUD、凭证、开关、限频、测试发送；渠道为**插件式**，新渠道=新适配器 |
| 第三方 App 可接入 | 推送中心提供**推送 API + 推送 token**（第三方**自助注册推送中心账号**，按角色管理应用/token，Bearer 鉴权调用）；网关独立前缀 + 应用/token 双层频控 + IP 白名单可选 |
| **第三方权限只限推送中心** | 第三方账号是**推送中心域内独立身份**，不并入平台主 RBAC——能用的只有：注册应用、申请/轮换 token、管理本方主题、查本方消息与用量；主站菜单/业务 API/其他应用数据一概不可见不可调（接口层按身份域硬隔离 + 数据层按归属过滤双保险） |
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
push_account      推送中心账号（第三方自助注册，独立身份域）：email(unique), phone,
                  password_hash(bcrypt), org_name, contact, status(pending 待审批/active/suspended),
                  created_at, approved_by(admin 审批留痕)
push_account_role 账号角色（只授推送中心域内权限）：account_id, role(developer/operator/viewer),
                  scope_app_id(可选，限定只管某应用；空=名下全部应用)
push_app          接入应用：owner_account_id(归属第三方) 或 NULL(平台自有)，name,
                  allowed_channels jsonb, rate_limit_per_min, ip_allow, enabled, created_by
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

**第三方账号与角色模型**（v1.4 新增）：
- **独立身份域**：`push_account` 与平台主 RBAC 的用户体系**完全分离**——不产生 `users` 行、不进 `ROLE_MENU_MAP`、拿不到主站 JWT；推送中心 portal 的会话令牌只被 `/api/push/portal/*` 认，调其他任何业务接口一律 403。
- **自助注册 + 审批制**：第三方在 portal 注册（邮箱+密码+机构名）→ 状态 `pending` → 平台 admin 审批转 `active` 后才能建应用/申请 token（防匿名滥用微信额度）。
- **角色三档（仅推送中心域内权限）**：

| 角色 | 能做什么（域内全集） |
|---|---|
| `developer` | 建/管名下应用、申请/轮换/吊销 token、注册主题、send 调用、查本方消息与投递日志 |
| `operator` | 主题管理、查本方消息/统计/失败重试触发（不能建应用、不能碰 token） |
| `viewer` | 只读：本方发送量/成功率/token 到期提醒 |

- **权限双保险**：portal 每个接口①按角色白名单放行（接口层硬编码，同现有"页面 API 角色白名单"惯例）②按 `owner_account_id/scope_app_id` 数据过滤（防越权看别家）。第三方"能碰的只有推送中心这些"是设计底线，不是配置项。
- **平台自有应用**（student app 等）`owner_account_id=NULL`，仍由主站 admin 在管理后台维护，与第三方门户互不越界。

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

**接入模型（v1.4 定稿）**：第三方在**推送中心 portal 自助注册账号**（`push_account`，邮箱+密码+机构名，admin 审批激活）→ 在自己名下创建应用 → 申请**推送 token**（选有效期档位）→ 拿 token 直接调推送 API。**不使用 HMAC 签名方案**（链路简单；https 传输 + Bearer token 满足内部平台间调用安全）；**第三方账号权限只覆盖推送中心域**，与主站 RBAC 身份域硬隔离。

```
鉴权：Authorization: Bearer <push_token>
      token 格式 pth-<32位随机>；PG 存 SHA-256 哈希，明文仅签发时显示一次

── 数据面（token 鉴权，给第三方服务器程序调）──
POST /api/push/open/send
Header: Authorization: Bearer pth-xxxx
Body:   { topic_code, receiver, title, body, payload?, channel_types?, ttl_seconds? }
Resp:   200 { msg_id, status }
        401 { code: TOKEN_INVALID | TOKEN_EXPIRED }   ← token 无效/过期，过期信息含 expired_at
        422 未注册主题    429 超 token QPS/应用频控
GET  /api/push/open/topics        本应用已注册主题列表
GET  /api/push/open/token-info    当前 token 的档位/剩余有效期（接入方自查）

── 管理面（portal 会话鉴权，给第三方网页控制台用；独立于主站 JWT）──
POST /api/push/portal/register    自助注册（→pending，图形验证码防脚本）
POST /api/push/portal/login       登录（bcrypt 校验，签发推送中心域内会话令牌）
── 以下需 active 账号 + 按角色白名单放行 ──
CRUD /api/push/portal/apps        名下应用（developer）
POST /api/push/portal/apps/{id}/tokens    签发/轮换/吊销 token，档位四选一（developer）
CRUD /api/push/portal/topics      本方应用主题（developer/operator）
GET  /api/push/portal/messages /stats     本方消息/用量统计（三档角色按只读梯度）
```

**推送 token 有效期档位（portal 申请时四选一，不支持永不过期）**：

| 档位 | 适用 |
|---|---|
| **1 个月** | 联调试用、临时活动 |
| **3 个月** | 短周期项目 |
| **6 个月** | 常规接入默认档 |
| **12 个月** | 长期稳定接入 |

token 生命周期规则：
- **过期即拒**：到期后 send 全 401 `TOKEN_EXPIRED`，网关/中间件层校验（PG+Redis 缓存 `push:tok:{hash}`，TTL 到 expired_at 自动失效，天然免改码过期）。
- **续期 = 换发**：不提供原地延期；portal"续期"按钮按所选新档位签发**新 token**，旧 token 保留 `grace_period=7 天` 过渡并行（防接入方换值窗口期断流），7 天后旧 token 立即失效。
- **随时吊销**：portal/admin 一键吊销立即 401，重发即可；应用禁用联动全部 token 失效；账号 suspended 联动名下全部应用 token 失效。
- **限额挂 token**：每 token 独立 QPS/日配额（默认沿用应用级，可在 token 上单独收紧），防止一个接入方打爆微信日额度。

- 网关 `gateway/config.yaml` 新增前缀：`/api/push/open/`（push_token 鉴权）、`/api/push/portal/`（portal 会话鉴权，独立于主站 RBAC）；限流按应用+token 双层，portal 注册/登录接口单独更严限流防爆破。
- 管理接口 `/api/admin/push/*` 走既有 rbac + 强制租户，仅 admin（管审批、全局视图、平台自有应用；可见第三方用量但**不能读其消息正文**，隐私口径）。
- 平台内业务（作业/判分）不走 HTTP 自环，直接调 `app/services/push_hub/emit.py`（同参数同语义，内部生成 msg_id 后同一 Kafka 管道）——**内外一条路，杜绝双实现漂移**。

## 六、B 端管理页（挂 RBAC 菜单，仅 admin）

| 页面 | 功能 |
|---|---|
| 推送总览 | 今日发送量/各渠道成功率/失败 Top、**按主题维度统计**、Kafka lag、**临期 token 提醒区** |
| 接入应用 | push_app CRUD（名称/allowed_channels/应用频控/禁用）→ **推送 token 签发弹窗：选 1/3/6/12 个月档位，明文仅显示一次**；token 列表（档位、签发/到期、剩余天数、QPS 配额、**续期=换发**、吊销） |
| **推送主题** | push_topic CRUD（应用内注册制）、主题级渠道路由/默认 TTL/日频控/force_channels 标记 |
| 渠道管理 | 渠道 CRUD、凭证配置（密文存储不回显）、有效期提醒、**单渠道测试发送** |
| 消息查询 | push_message 分页（按应用/**主题**/接收人/状态/时间）、重发死信、手动过期 |
| **第三方账号审批** | push_account 列表（pending 审批开/驳回、active/suspended 停启）、账号角色授予、名下应用/token 总览与紧急吊销；**只见统计不见消息正文** |

> 第三方 portal（独立门户）：注册/登录 + 我的应用 + 我的 token + 我的主题 + 用量报表，共 5 页；部署同 web-frontend 形态但**独立构建产物**（不与主站 bundle 混编，会话域隔离）。主站管理页与 portal 各自走各自的前端三要素；portal 不进主站 RBAC 菜单。

前端三要素按项目铁律执行：`router/index.js` + `menu.js MENU_ROUTE_MAP` + `rbac_seed.py MENUS/ROLE_MENU_MAP`，缺任一项必被 `hasRouteAccess` 弹回。

## 七、student app（第一接入端）改造

- **平台自有应用**（`owner_account_id=NULL`，主站 admin 维护，不占第三方 portal）；同时作为第三方接入的示范样板。预置 5 个主题：`homework`（作业下发）、`grade`（判分完成，微信+App 双通道）、`wrongbook`（错题复盘）、`system`（系统公告）、`practice`（练习提醒，预留）。
- 消息中心页 `MessagesScreen.kt`：**顶部主题 Tab**（全部/作业/成绩/错题/公告）+ 底部「我的」未读 Badge（总数，展开看分主题）+ 前台 25s 长轮询（OkHttp readTimeout 设 30s，客户端超时必须 > 服务端挂起）。
- 消息设置页：按主题静音开关（写 push_subscription）。
- 判分完成/作业下发业务钩子调内部 emit 带对应 topic_code（替代 v3.0 计划的 student_message 表——被 push_message 通用模型取代）。

## 八、安全与风险

- **token 只存哈希**（SHA-256，明文仅签发时显示一次）、portal 密码 bcrypt(cost≥10)、渠道凭证 Fernet 密文（密钥走环境变量），日志脱敏；PushPlus token/极光 master secret 不进库不进 git。
- **身份域隔离（v1.4 底线）**：push_account 会话令牌与主站 JWT 互不互认（签发密钥/受众声明分离），portal 令牌调 `/api/admin/*`、业务 API 一律 403；主站 RBAC 用户调 portal 接口同样 403。第三方权限闭包=推送中心域内接口白名单，**新增接口默认不进白名单**。
- **注册风控**：portal 注册限频 + 图形验证码 + admin 审批制；审批前一切写接口 pending 拒绝；同邮箱/IP 重复注册拦截。portal 登录失败锁定（沿用网关暴力破解防护口径）。
- **越权双保险**：portal 全部查询强制 `owner_account_id/scope_app_id` 过滤（SQL 层，非前端裁剪）；测试用例必须包含"A 账号读 B 应用消息返回 404"。
- **token 滥用防线**：https 传输 + 应用级频控 + token 级 QPS/日配额 + 档位有效期自然淘汰 + 一键吊销 + 禁用/停号联动；last_used_at 便于发现僵尸 token 定期收编。
- **幂等**：msg_id unique + Kafka at-least-one，dispatcher 重复消费只投一次；测试必锁。
- **Redis 是加速层**：所有计数/缓存可自 PG 重建；照 `cache.py` 惯例静默降级，禁止 Redis 挂 → 接口 500。
- **CF 30s**：长轮询上限 25s，测试断言响应 <30s。
- **推送内容脱敏**：title/body 不放大额/成绩数值，详情点开走接口。
- **内存**：dispatch_worker 随主进程，heap 增量≈0；Kafka 复用现有集群不新建。
- 合规：微信渠道沿用 PushPlus 实名前提（一次性收费、终生有效）；第三方接入的推送内容由接入方自负，平台留 push_delivery_log 可溯源。

## 九、分期与工作量

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P1 推送中心内核 ✅ | 9 张表（含 **push_account/push_account_role/push_token/push_topic/push_subscription**）+ **第三方账号域**（注册/审批/登录/角色白名单/portal 会话令牌与主站 JWT 双域隔离）+ **推送 token 签发/校验中间件（档位 1/3/6/12 月、Redis TTL 过期、吊销/停号联动）** + emit/OpenAPI 带 topic_code(Kafka+降级+幂等+主题注册校验) + dispatch_worker（**主题渠道路由+静音+主题频控**）+ **app_offline 渠道**（收件箱/**主题 Tab 接口/分主题未读**/长轮询）+ 四类有效期 | 5–7 天 |
| P2 管理端×2 ✅ | 主站 6 页（+第三方账号审批）+ **第三方 portal 独立门户 5 页**（注册/登录/应用/token/主题/用量）+ RBAC 三件套（portal 不走主站菜单）+ 渠道凭证加密 + 测试发送 | 3–4 天 |
| P3 微信渠道 ✅ | **wechat_oa 适配器**（`WechatOAProvider` 抽象 + PushPlus 实现，token 管理员配置化+有效期）+ vendor/webhook 适配器接口占位 | 1–2 天 |
| P4 接入验证 | student app 消息中心（主题 Tab+静音设置）改造 + 一个第三方 demo 走通 **注册→审批→建应用→token→send 全链** + 越权/隔离用例 + 发版冒烟 | 2–3 天 |
| 合计 | | **11–16 天** |

> 依赖顺序：P1 → P2/P3 并行 → P4。股票盯盘计划里的 PushPlus 直调改为消费推送中心的 wechat 渠道（避免两处各接一套）。

## 十、验收清单

- [x] 第三方 portal 自助注册 → pending 不可写 → admin 审批 active → 建应用/签发 token/调 send 全链跑通（单测 test_full_lifecycle + 生产 p2smoke 账号 pending 验证）
- [x] **身份域隔离**：portal 会话令牌调 `/api/admin/*` 与业务 API 全 403；主站 JWT 调 portal 接口 403；第三方角色白名单外接口（含未列入的后续新接口）默认 403（test_three_auth_realms_do_not_interop）
- [x] **越权双保险**：A 账号查 B 应用的消息/token 返回 404/403（SQL 层过滤）；operator/viewer 角色不能碰 token/应用写操作（test_cross_account_isolation_404 / test_role_gate_viewer）
- [x] 第三方拿**推送 token**（Bearer）send 一条（带 topic_code）→ 接收端 App 对应主题收件箱内可见（离线不丢）——**P4 生产 demo 全链 ALL PASS**：pth-token（Bearer）经网关 send → grade 消息落库 → `/api/student/push/messages` unread=1 → 已读归零 → 他人 total=0（隔离）；App 端 MessagesScreen 主题 Tab + 长轮询已编译出包（app-debug.apk 16M）
- [x] token 生命周期全链路：签发选 1/3/6/12 月档→明文只显示一次→过期 send 401 `TOKEN_EXPIRED`→续期换发旧 token 7 天宽限并行→吊销即时 401→禁用应用/停号联动全 token 失效（test_token_expiry / rotate_grace / suspend_kills / app_disable 四用例）
- [x] 主题路由生效：`grade` 主题双通道配置（生产 app#1）、未注册主题拒发 422、主题停用 403（test_topic_not_registered_422 / admin_readonly toggle）；日频控超限行为待高并发实测
- [ ] 收件人对主题静音后：app_offline 不计数但落库可查；force_channels 主题微信仍触达——**P4：静音开关 UI 在 App 消息设置页**（后端 routing.resolve_channels 逻辑已实现）
- [x] `wechat_oa` 渠道：管理员配好 PushPlus token 后测试发送，微信秒级收到（P3 生产实发 status=sent + delivery_log ok）；receiver=他人 32 位令牌自动走好友 `to` 转发，对方未实名/未加好友时 `905/999` 标 dead、留痕可查、支持重发
- [x] 前台长轮询 poll 服务端 25s 上限（<CF 30s）实现并有测试锁（test_poll_returns_fast）；**P4 App 端已接**：PushStore 前台循环续拉（OkHttp readTimeout=180s > 25s）、游标微秒防重复、底部栏消息 Tab 未读角标；Redis 缺席计数回源 PG 即测试环境常态路径
- [x] **判分完成/作业下发业务钩子**接推送中心：`business.py` 独立会话 fire-and-forget + 幂等 msg_id（同 submission 只推一次）+ 总开关 `push_business_notify_enabled`；生产验证 grade 主题落库 status=partial（微信引流失败不影响 App 收件箱）、重复触发 row count=1；修 `bump_inbox_counters` 未失效 agg 未读键的脏读 bug（test_new_message_invalidates_unread_agg_cache）
- [x] 重复投递同 msg_id 只发一次（duplicate）；过期消息（expire_at）不再投递（deliver 头部终态出口 + expired 留痕）
- [x] 主站管理页：审批账号/建应用/签发 token/配两种渠道/改主题/禁用/测试发送全闭环（P2 已上线，SELF 测试发送生产 200）；token/密码/渠道凭证均密文/哈希存储不回显（test_channel_credentials_encrypted）；admin 只见第三方统计不见消息正文
- [x] Kafka 停用时 emit 降级直写、消息不丢；接口无 500（全部单测跑在无 Kafka 环境=降级路径即被测路径）
- [x] 前端单测 + 后端全量通过（含身份域隔离/越权用例）；发版 137 冒烟通过，回滚点 bak-20261119 已打（2026-11-19 16:20 生产核验：容器 Up + 网关三链路 200/401/409 全对）

---

*v1.4 增补决策沿革：第三方由"admin 代为注册应用"升级为**自助注册推送中心账号（push_account）+ 三档角色（developer/operator/viewer）+ admin 审批制**，权限闭包限定推送中心域内接口，与平台主 RBAC 身份域硬隔离。本计划基于 2026-11-19 代码库实测（Kafka/Redis/worker/cache 惯例/网关前缀鉴权均为现成件）；《student-app消息推送实施计划》v3.0 并入本文档，student app 为第一接入端。*
