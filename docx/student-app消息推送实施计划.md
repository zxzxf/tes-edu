# student app 消息推送实施计划（自建 Kafka + Redis 版）

> 版本：v3.1 ｜ 日期：2026-11-19 ｜ 状态：**已并入《通用App推送中心实施计划》**
> ⚠️ 2026-11-19 决策升级：本方案不再单独实施。推送能力上升为**平台级通用推送中心**（管理员可配置渠道、第三方 App 可接入、消息/授权可设有效期），student app 降为推送中心的第一个接入端。实施以 `docx/通用App推送中心实施计划.md` 为准；本文中"架构、Kafka/Redis 复用、幂等、CF 30s、长轮询 25s、内存约束"等结论已被其吸收，不再重复维护。
> 目标：不依赖任何第三方推送 SDK（极光/个推/PushPlus 均不用于学生端），**自建 Kafka + Redis 消息管道**实现学生端消息的可靠投递：业务事件异步解耦、离线消息持久化补收、在线未读秒级可见。
> 决策沿革：v1.0 选极光 → v2.0 改纯站内轮询 → **v3.0 用户拍板：自建 Kafka + Redis**（复用 137 现成基建）。

---

## 一、先说清"离线推送"在这套方案里的准确含义

| 能力 | 本方案 | 说明 |
|---|---|---|
| 消息**离线可达**（App 关掉期间产生，下次打开全部补收、不丢） | ✅ | Kafka 削峰 + PG 持久化 = 离线消息收件箱，这是"离线"的正解 |
| 在线**准实时**（App 开着时秒级收到） | ✅ | 前台 25s 长轮询（刻意避开 Cloudflare 30s 边缘超时——502 事故的教训）+ Redis 未读计数 |
| **系统通知栏弹提醒**（App 被杀也弹） | ❌ | 技术上必须走厂商通道/FCM，自建集群无法向未启动的 App 注入系统通知。此为边界，明确不承诺 |

结论：自建管道把"可靠 + 不丢 + 准实时"全部解决，唯一让渡的是"杀进程弹通知"——将来若确需，PG 里的消息表就是厂商推送的待发队列，同一套 emit 加一条 consumer 即可升级。

---

## 二、现状（基于代码实测，全部复用）

| 组件 | 现状 | 复用方式 |
|---|---|---|
| Kafka | 137 已在跑（opt_default 网络，heap 已调小适配 2G 内存）；topics：`course_kb_ingest`、`student_event`、`explanation_video_gen` | 新增 topic `notify_events`，沿用 `web-backend/app/core/kafka_client.py` 的 producer/consumer 封装（aiokafka） |
| Redis | 106 主实例，web-backend 经 `redis://:<pw>@redis:6379/0` 使用（`app/core/cache.py` 已有带降级惯例：Redis 挂了直查 DB） | 未读计数 + 消息收件箱缓存，key 前缀 `notify:` |
| Worker | `student_event_worker.py` 模式：可随 main.py lifespan 启动，也可独立跑；单条失败不中断消费 | 新增 `notify_worker.py` 完全照此模式 |
| 降级先例 | 讲解视频链路：未配 Kafka 时自动走 BackgroundTasks 同步 | 本链路照做：无 Kafka 时 BackgroundTasks 直写 PG+Redis |
| 鉴权 | 网关 `/api/` rbac + 学生身份头注入 | 学生侧读接口只放行本人 student_user_id |

---

## 三、架构

```
web-backend 业务钩子（作业下发/判分完成/复盘提醒/公告）
    │ emit（BackgroundTasks 不阻塞响应；Kafka 不可用则直写降级）
    ▼
Kafka topic: notify_events        （削峰、解耦、至少一次）
    ▼
notify_worker（aiokafka consumer，group=notify-consumer，幂等 by event_id）
    ├─▶ PG student_message         （唯一事实源：持久化、分页、已读状态 → 离线补收）
    └─▶ Redis
        ├─ notify:unread:{sid}     INCR 未读计数（列表页秒查，TTL 7d，miss 回源 PG）
        └─ notify:box:{sid}        最近 50 条 ZSet 收件箱热缓存（读加速，可重建）
    ▼
student app
    ├─ 打开/切页/下拉 → GET /messages（PG 分页 + Redis 未读数）
    ├─ 前台常驻 → GET /messages/poll?since= 长轮询 25s 返回新消息
    └─ 未读角标 Badge；点击按 type 跳作业详情/成绩页/错题本
```

**为什么 Redis 不能当唯一存储**：Redis 只做计数与热缓存，宕机重建无感；消息本体必须落 PG——否则 Redis 一重启离线消息就丢，"离线可达"就不成立了。

## 消息类型（本期 4 种，与 v2.0 一致）

| type | 触发点 | 点击行为 |
|---|---|---|
| `homework_new` | 教师布置作业 | 跳作业详情 |
| `homework_graded` | 判分完成（exam 判分链路） | 跳成绩/解析页 |
| `wrongbook_digest` | 每日 19:00 worker 按班汇总 | 跳错题本 |
| `system` | admin 后台公告 | 跳详情或无跳转 |

---

## 四、实施步骤

### 阶段 1 · 消息管道（web-backend，2–3 天）
| # | 文件 | 改动 |
|---|---|---|
| 1.1 | `app/core/config.py` | 加 `kafka_topic_notify: str = "notify_events"`、`kafka_group_notify: str = "notify-consumer"`（沿用 `kafka_enabled()` 判定） |
| 1.2 | `app/services/notify.py`（新增） | 单点 `emit(student_ids, type, title, body, payload)`：生成 event_id（uuid）→ 优先投 Kafka（复用 `kafka_client.publish`，5s 超时、失败记日志不抛）；Kafka 未启用时直写 PG+Redis 降级（照抄讲解视频先例） |
| 1.3 | `app/models/` + `migrations/00NN_student_message.sql` | `student_message`：id、event_id **unique**（幂等防 Kafka at-least-one 重复消费）、tenant_id、student_user_id、type、title、body、payload jsonb、read_at、created_at；索引 `(student_user_id, created_at desc)`、部分索引 `(student_user_id) where read_at is null` |
| 1.4 | `app/workers/notify_worker.py`（新增） | 消费 `notify_events` → 逐条幂等插 PG（event_id 冲突跳过）→ `INCR notify:unread:{sid}`、`ZADD notify:box:{sid}`（保留 50 条、TTL 7d）；单条失败不中断（照抄 student_event_worker）；随 main.py lifespan 启动，亦可独立跑 |
| 1.5 | 学生侧 API（`app/api/` 新增 `notify.py`） | `GET /messages`（分页 + 未读数：Redis 优先、miss 回源 PG count 并回填）、`POST /messages/{id}/read`、`POST /messages/read-all`（ DECR/DEL 计数）、`GET /messages/poll?since_id=`（长轮询：Redis pub/sub `notify:ch:{sid}` 挂起 ≤25s 或有新即返，超时空返）；全部强校验只操作本人 |
| 1.6 | 业务钩子 | 判分完成链路 + 作业下发链路调 `notify.emit`；每日 19:00 crontab 脚本按班查错题生成 `wrongbook_digest`（批量 emit，一次多 alias 不逐条） |
| 1.7 | 测试 | emit 走 Kafka/降级两路、worker 幂等（同 event_id 消费两次只落一条）、未读计数与回源、长轮询 25s 上限、越权 403、read-all 幂等 |

### 阶段 2 · App 端消息中心（student-app，1–2 天）
| # | 文件 | 改动 |
|---|---|---|
| 2.1 | `data/remote/ApiService.kt` | 加 4 个接口（list/read/readAll/poll） |
| 2.2 | 新建 `ui/screens/MessagesScreen.kt` | 消息列表（未读加粗圆点）、点击按 type 路由、全部已读；Compose 沿用现有范式 |
| 2.3 | `MainActivity.kt` | 底部导航「我的」Badge 未读角标；App 进入前台起 poll 循环（OkHttp readTimeout 设 30s > 25s 长轮询上限，切后台停——**注意 OkHttp 超时与 CF 30s 关系已在判分 502 踩过，客户端超时必须大于服务端挂起**） |
| 2.4 | 本地 | 未读数/最近列表落 Room，弱网先渲染缓存（可选） |

### 阶段 3 · 联调发版（0.5–1 天）
- Kafka 建 topic（137 容器内 kafka-topics 脚本，或沿用自动创建）；迁移走容器内 asyncpg 拆分执行器（项目惯例）。
- `deploy/publish_backend.sh web` 发 137；重打 debug APK 真机验证。
- 冒烟：教师判分 → Kafka lag 秒级消费 → 学生 App 前台 25s 内未读+1；杀掉 App → 再布置两条作业 → 重开 App 两条全部补收；Redis 手动 FLUSHDB notify:* 后计数回源正确。

---

## 五、风险与注意

- **内存红线**：137 available 仅 1.8G，kafka/zk heap 已调小。notify_worker **随 web-backend 主进程 lifespan 起**，不新建容器，增量≈0；独立容器方案被否的原因就在这。
- **幂等是硬要求**：Kafka at-least-one 必然可能重复投递，`event_id unique` 是消费端唯一正确的姿势，测试必锁。
- **长轮询别超 25s**：Cloudflare 边缘 30s 超时（判分 502 根因），服务端挂起上限、客户端 OkHttp 超时必须按此对齐，并在测试里断言 poll 响应时间 < 30s。
- **Redis 是加速层不是数据源**：所有 Redis key 可从 PG 重建；`cache.py` 已确立"Redis 故障静默降级直查 DB"惯例，notify 侧照抄，禁止出现"Redis 挂了消息接口 500"。
- **未读计数漂移**：INCR/DECR 与 PG 可能漂移（worker 重放、手动清库），列表接口以 PG count 为准定期校准 Redis（read-all 直接 DEL 计数键回源）。
- **payload 不放敏感数据**：成绩详情走接口现拉，消息只放"已批改，点击查看"。
- **升级预留**：将来接厂商推送（若验收要求杀进程弹通知），新增一个 consumer 读同一 `notify_events` 调厂商 API 即可，业务代码零改动——这是本架构比 v2.0 纯轮询多花的钱买到的东西。

---

## 六、工作量汇总

| 阶段 | 相对工作量 |
|---|---|
| 1 消息管道（Kafka+Redis+PG+API+钩子） | 2–3 天 |
| 2 App 消息中心 + 长轮询 | 1–2 天 |
| 3 联调发版冒烟 | 0.5–1 天 |
| 合计 | **3.5–6 天**，零第三方费用、零外部账号注册 |

---

*v3.0 基于 2026-11-19 代码实测编写：Kafka/Redis/worker/降级四项基建均为仓库现成件，本方案是"组装复用"而非新建。v1.0 极光 JPush、v2.0 纯站内轮询均被取代，决策沿革见第一节抬头。*
