# Redis 跨云主从架构任务计划书（137 ↔ 106）

> 项目：Tes Edu 课程教育 Agent
> 撰写：2026-10-22　状态：**待实施**（本文档为计划书，未改动任何线上配置）
> 目标读者：运维负责人 / 后端负责人

---

## 一、背景与目标

当前 137（腾讯云）与 106（百度云）各运行一套独立 Redis，互为孤岛：

- **137 `edu-redis`**：网关生产依赖——全局限流令牌桶（`gw:rl:*`）+ 动态配置热更新（`gw:cfg:*`）
- **106 `srv-redis`**：全新空实例，零 key、零客户端，尚未投入使用

**目标**：将两台 Redis 组成跨云主从复制，实现：

1. **容灾（DR）**：137 的 Redis 故障时，106 保有近乎实时的副本可提升为主库，网关可切换
2. **读扩展预留**：106 侧未来业务（题库/RAG 检索结果缓存）可直读本地副本，延迟内网级
3. **数据零冲突**：106 实例当前为空，纳入复制无存量数据冲突

**非目标**（本期不做）：跨云多活双写、自动故障转移（Sentinel/Proxy）、TLS 加密复制（列为演进项）。

---

## 二、现状调研结论（2026-10-22 实测）

### 2.1 双机参数对比

| 项目 | 137 `edu-redis` | 106 `srv-redis` |
|---|---|---|
| 部署方式 | docker compose（`gateway/docker-compose.yml`） | docker compose（`/opt/deploy/docker-compose.yml`） |
| 版本 | Redis **7.4.11** | Redis **7.4.11**（版本一致，复制兼容 ✅） |
| 当前角色 | master（connected_slaves: 0） | master（空闲） |
| 鉴权 | `requirepass`，32 位强密码（环境变量注入） | `requirepass Tes@Redis#106`（启动参数明文，见风险 R6） |
| 内存策略 | maxmemory 256MB，allkeys-lru | maxmemory 512MB，allkeys-lru |
| 持久化 | AOF everysec + RDB（900/1、300/10、60/10000） | AOF yes（命令行启动，未挂载 conf，无 RDB 规则） |
| 端口绑定 | `10.0.0.5:6379`（仅腾讯云内网，**公网不可达**） | `0.0.0.0:6379`（公网可达，有密码） |
| 数据量 | used_memory 1.54M，DBSIZE 3 | 0 key，0 客户端 |
| 宿主系统 | CentOS 7 / 腾讯云 124.221.253.137 | CentOS 8.4 / 百度云 106.12.25.5 |

### 2.2 连通性矩阵（实测）

| 方向 | 端口 6379 | 端口 22 | 说明 |
|---|---|---|---|
| 137 → 106 | ✅ REACHABLE | — | RTT **7.4ms**（min/avg/max = 7.37/7.40/7.44ms，0 丢包） |
| 106 → 137 | ❌ BLOCKED | ✅ REACHABLE | 137 的 Redis 只发布在内网 10.0.0.5，公网无人监听 |

**关键推论**：复制链路方向必须是「**106 主动连接 137**」（Redis 复制由从库发起），因此需要先打通 106 → 137:6379（见方案 A/B）。

### 2.3 网关对 Redis 故障的耐受度（代码级确认）

`gateway/internal/ratelimit/ratelimit.go:133-147`：Redis 异常时**回退本地令牌桶**（fail-open，限流精度降级但不阻断请求）。configsync 同样防翻转（读取失败保留旧快照）。

**结论**：主从切换 / 重建期间业务**不中断**，仅限流精度短时降级——这是本方案可行的重要前提。

---

## 三、方案设计

### 3.1 拓扑与方向论证

```
                    公网（RTT 7.4ms，安全组白名单）
  ┌─────────────────────────┐        ┌─────────────────────────┐
  │ 137（腾讯云）            │        │ 106（百度云）            │
  │                         │        │                         │
  │  edu-redis  (MASTER)    │◄───────│  srv-redis  (REPLICA)   │
  │  :6379  读写             │ 复制流  │  :6379  只读             │
  │                         │        │                         │
  │  gateway ×2 ──内网读写──►│        │  未来：题库缓存直读副本   │
  └─────────────────────────┘        └─────────────────────────┘
```

**为什么 137 为主、106 为从**（方向不可反）：

1. 生产写入方（gateway 限流桶、配置 seed）全在 137，写必须落在本地（内网 <1ms）；反向则每次限流付 7.4ms 公网延迟且暴露于链路抖动
2. Redis 从库**只读**，gateway 的令牌桶扣减是写操作，137 绝不能当从库
3. 106 实例为空，当从库零冲突、零迁移成本

### 3.2 连接方案选型（106 → 137:6379 打通方式）

| | 方案 A：公网白名单直连（**推荐**） | 方案 B：SSH 隧道（保守备选） |
|---|---|---|
| 做法 | 137 redis 端口发布到公网 IP，腾讯云安全组仅放行 `106.12.25.5/32` | 106 侧 autossh 建 `127.0.0.1:6379 → 10.0.0.5:6379` 隧道，`replicaof 127.0.0.1 6379` |
| 改动 | 137 compose ports 一行 + 安全组一条规则 | 106 加 systemd 常驻服务 + 137 零改动 |
| 性能 | 直连，无损 | 多一跳 SSH 加密，吞吐/延迟略增 |
| 安全面 | redis 端口对 1 个 IP 可见（强密码 + 白名单） | redis 完全不暴露公网（最小暴露面） |
| 稳定性 | 依赖安全组规则长期有效 | 隧道进程是单点，需 autossh + 守护 |
| 回退 | 改回 `10.0.0.5:6379` 绑定 | 停 systemd 服务即回退 |

**建议**：首选 **A**（简单、可观测、运维动作少；强密码 + /32 白名单的风险可控），B 作为安全评审不过时的退路。若后续通过安全评审要求「数据库类端口禁止公网发布」，切换 B 的代价仅是 106 上一个 systemd unit。

### 3.3 安全设计

- **鉴权**：master 侧复用 137 现有 32 位 `requirepass`；slave 侧配置 `masterauth`（从库凭它向主库认证）。双方各自的 `requirepass` 独立，客户端访问 106 从库仍用 `Tes@Redis#106`
- **网络**：安全组规则精确到 `/32`；不用 `0.0.0.0/0`；106 侧 redis 当前 `0.0.0.0:6379` 公网暴露且暂无客户端，**建议趁本次收紧为 `127.0.0.1:6379`**（从库复制由 106 主动出站，不受影响）
- **文档脱敏**：本文档所有密码以 `<PW_137>` / `<PW_106>` 占位；实际值从 137 gateway 容器 env `REDIS_PASSWORD` 与 106 部署方密钥管理员获取，**禁止写入任何仓库文件**

### 3.4 容灾设计（手动提升 + 为什么不上 Sentinel）

**判定与提升 Runbook**（106 侧执行）：

```bash
# 1. 确认与主库失联（master_link_status:down 持续 > 60s）
redis-cli -a "$PW_106" --no-auth-warning INFO replication | grep -E "master_link_status|master_last_io"
# 2. 提升为主（切断复制关系，本地数据即成主库）
redis-cli -a "$PW_106" --no-auth-warning REPLICAOF NO ONE
# 3. 137 侧切换 gateway 指向（REDIS_ADDR 改为 106 公网:6379，实测 137→106:6379 可达）
#    cd /opt/tes-edu-gateway && REDIS_ADDR=106.12.25.5:6379 docker compose up -d --scale gateway=2
```

**本期不引入 Sentinel 的理由**：两节点跨云场景 Sentinel 法定人数（quorum）尴尬——奇数哨兵需 3 实例，跨云脑裂风险高于收益；且网关已有 Redis 故障 fail-open 兜底，手动提升 + 监控告警的 RTO（约 5 分钟）对当前 POC/早期生产可接受。Sentinel 列入演进项（见 §九）。

**切换期业务影响**：限流桶状态丢失 → 桶从零重建（fail-open 期间不阻断）；`gw:cfg:*` 配置快照由 configsync 防翻转保留，127 秒内（Pub/Sub 重连）恢复热更能力。**业务无感知**。

---

## 四、前置条件清单

| # | 条件 | 负责方 | 状态 |
|---|---|---|---|
| P1 | 腾讯云安全组新增规则：放行 106.12.25.5/32 → tcp:6379 | 运维 | 待办 |
| P2 | 确认 137 redis 公网发布的安全评审结论（方案 A/B 定稿） | 运维 + 安全 | 待办 |
| P3 | 获取 `<PW_137>`（137 容器 env `REDIS_PASSWORD`）与 `<PW_106>` 的密钥交接 | 运维 | 待办 |
| P4 | 操作窗口：低峰期，137 redis 容器重建约 10s 不可用（gateway 自动降级本地桶） | 运维 | 待办 |
| P5 | 106 `/opt/deploy/docker-compose.yml` 纳入 git 或备份（当前无版本管理） | 运维 | 待办 |

---

## 五、实施步骤（方案 A）

### 阶段 1：打通链路（137 侧，约 15 分钟）

1. 腾讯云控制台 → 安全组 → 入站规则：`106.12.25.5/32` → TCP 6379，备注 `redis-repl-from-106`
2. 修改 137 `gateway/docker-compose.yml`：

   ```yaml
   ports:
     - "124.221.253.137:6379:6379"   # 原 "10.0.0.5:6379:6379"；公网发布，安全组 /32 白名单兜底
   ```

3. 重建 redis 容器（**仅 redis，勿动 gateway/lb**）：

   ```bash
   cd /opt/tes-edu-gateway && docker compose up -d --no-deps --force-recreate redis
   ```

4. 106 侧验证连通：`redis-cli -h 124.221.253.137 -p 6379 -a "$PW_137" --no-auth-warning PING` → PONG

### 阶段 2：建立复制（106 侧，约 10 分钟）

1. 备份 106 compose：`cp /opt/deploy/docker-compose.yml /opt/deploy/docker-compose.yml.bak.$(date +%F)`
2. 修改 106 `/opt/deploy/docker-compose.yml` 的 redis 服务 command，追加两参数：

   ```yaml
   command: ["redis-server", "--appendonly", "yes", "--maxmemory", "512mb",
             "--maxmemory-policy", "allkeys-lru", "--requirepass", "${REDIS_PASSWORD}",
             "--replicaof", "124.221.253.137", "6379",
             "--masterauth", "${MASTER_REDIS_PASSWORD}"]   # = <PW_137>
   ```

   并在同文件 environment 段（或 systemd EnvironmentFile）提供 `MASTER_REDIS_PASSWORD`
3. 重建：`cd /opt/deploy && docker compose up -d --no-deps --force-recreate redis`
4. 观察启动日志：`docker logs srv-redis --tail 20`，应出现 `MASTER <-> REPLICA sync` 相关且最终 `Ready to accept connections`

### 阶段 3：验证（双机核对，见 §六 清单）

### 阶段 4：监控常驻（约 20 分钟）

部署 106 侧探活脚本（或并入现有监控）：

```bash
# /usr/local/bin/redis-repl-check.sh —— 输出 0=健康，非 0=异常（供 cron/告警接入）
st=$(redis-cli -a "$PW_106" --no-auth-warning INFO replication | grep -oP 'master_link_status:\K\w+')
lag=$(redis-cli -a "$PW_106" --no-auth-warning INFO replication | grep -oP 'master_repl_offset:\K\d+')
moff=$(redis-cli -h 124.221.253.137 -p 6379 -a "$PW_137" --no-auth-warning INFO replication | grep -oP 'master_repl_offset:\K\d+')
[ "$st" = up ] && [ $((moff - lag)) -lt 10000 ] && echo OK || { echo "REPL_BROKEN st=$st lag=$((moff-lag))"; exit 1; }
```

`*/1 * * * * /usr/local/bin/redis-repl-check.sh >> /var/log/redis-repl-check.log 2>&1`，连续 3 次异常触发告警（阈值 §六）。

### 阶段 5：容灾演练（选做但强烈建议，约 30 分钟）

1. 低峰期，137 侧 `docker stop edu-redis`
2. 60 秒后按 §3.4 Runbook 提升 106 为主并切换 gateway `REDIS_ADDR`
3. 验证：`/healthz` 200、限流仍工作（可能短时 429 放宽后恢复）、业务请求无 5xx
4. 恢复：137 redis 起来后，按需将 137 重新设为 106 的从库做反向同步，再按本文档方向切回（或保持 106 为主，更新文档）

---

## 六、验证清单

| # | 验证项 | 命令 / 方法 | 期望 |
|---|---|---|---|
| V1 | 主库见到从库 | 137：`INFO replication` | `connected_slaves:1`，`slave0:ip=106.12.25.5,port=xxxxx,state=online` |
| V2 | 从库链路健康 | 106：`INFO replication` | `role:slave`、`master_link_status:up` |
| V3 | 数据实时性 | 137 `SET repl_test:$(date +%s) 1`，106 `GET repl_test:*` | 秒级可见 |
| V4 | 复制延迟 | 双机对比 `master_repl_offset` | 差值 < 10,000（7.4ms RTT 下通常 < 1,000） |
| V5 | 从库只读 | 106 `SET x 1` | `(error) READONLY You can't write against a read only replica.` |
| V6 | 鉴权隔离 | 106 用错密码访问 | NOAUTH 拒绝；137 侧 masterauth 生效（无 `MASTERDOWN` 刷屏） |
| V7 | 全量同步日志 | 106 `docker logs srv-redis` | 一次 `Full resync` 后转 `Partial resynchronization` |
| V8 | 断链自愈 | 安全组临时摘除白名单 30s 再恢复 | `master_link_status` down→up 自动恢复，无需重启 |
| V9 | 业务无感 | 全程打点 `/healthz` 与 `/api/app/ping`（带 token） | 全程 200，无 5xx |

---

## 七、回滚方案

| 场景 | 动作 | 耗时 |
|---|---|---|
| 复制异常想整体回退 | 106：`REPLICAOF NO ONE`（运行时立即生效，回到独立空实例）；137 compose ports 改回 `10.0.0.5:6379` 后 recreate | 5 分钟 |
| 误操作弄脏从库数据 | 106：`REPLICAOF NO ONE` → `FLUSHALL` → 重新 `REPLICAOF` 触发全量同步 | 2 分钟 |
| 公网发布安全评审不过 | 整体切方案 B（SSH 隧道），137 侧零改动 | 30 分钟 |
| gateway 切错 REDIS_ADDR | compose 环境改回 `redis:6379` recreate gateway | 3 分钟 |

**回滚验证**：每步回滚后执行 §六 V1/V2/V9。

---

## 八、风险与注意事项

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | 137 redis 公网发布期被扫描/爆破 | 中 | 安全组 /32 白名单 + 32 位强密码 + 操作窗口尽量短；完成 V1-V4 后评估是否维持公网发布 |
| R2 | 跨云网络抖动导致复制频繁重连 | 中 | 监控 `master_link_status`；redis 自动重连；持续抖动则切方案 B |
| R3 | 复制中断期间 106 数据陈旧，误提升为主丢数据 | 高 | 提升前必须核对 `master_repl_offset` 差值（V4 脚本）与 `master_last_io_seconds_ago`，差值过大时人工决策 |
| R4 | 137 redis 容器重建瞬间 gateway 限流降级 | 低 | 已知 fail-open 行为（§2.3），业务无感；低峰期操作 |
| R5 | 副本内存行为差异（副本默认不参与淘汰，key 集合可能与主不一致观感） | 低 | 106 从库 `maxmemory` 建议显式调大或置 0 仅监听；本期数据量 1.5M 级，无压力 |
| R6 | 106 redis 密码 `Tes@Redis#106` 为启动参数明文（历史遗留），且 0.0.0.0 公网暴露 | 中 | 本期借机：绑定改 127.0.0.1、密码改由 env/密钥文件注入（P5 顺带完成） |
| R7 | CentOS 7（137）已 EOL，Redis 7.4 官方镜像无安全补丁通道 | 中 | 既有事实；列系统升级规划，不在本计划范围 |

---

## 九、后续演进路线（不在本期）

1. **TLS 复制**：Redis 7.4 原生支持 `tls-port` + `tls-replication yes`，公网复制流加密（需自签或申请证书，纳入证书管理流程）
2. **自动故障转移**：引入 Sentinel（3 实例，2 在北京区 + 1 在腾讯区）或跨云 VIP/Keepalived，配 `sentinel monitor` 自动 REPLICAOF 切换
3. **读写分离接入**：106 侧业务缓存读直连从库 `READONLY` 模式；配套 `WAIT 1 0` 强一致读（按业务取舍）
4. **跨云专线**：腾讯云 CCN + 百度云专线打通后，复制流转内网，可撤销公网发布（R1 归零）
5. **106 compose 版本化**：`/opt/deploy` 纳入 git 或配置管理，杜绝手工漂移

---

## 附录：命令速查

```bash
# —— 状态查看 ——
redis-cli -a "$PW_137" --no-auth-warning INFO replication      # 137（主）
redis-cli -a "$PW_106" --no-auth-warning INFO replication      # 106（从）

# —— 手动提升 / 回退 ——
redis-cli -a "$PW_106" --no-auth-warning REPLICAOF NO ONE      # 106 升主
redis-cli -a "$PW_106" --no-auth-warning REPLICAOF 124.221.253.137 6379   # 恢复为从

# —— 强制全量同步（从库数据可疑时）——
redis-cli -a "$PW_106" --no-auth-warning FLUSHALL             # 先 REPLICAOF NO ONE

# —— 延迟测量 ——
m=$(redis-cli -h 124.221.253.137 -a "$PW_137" --no-auth-warning INFO replication | grep -oP 'master_repl_offset:\K\d+')
s=$(redis-cli -a "$PW_106" --no-auth-warning INFO replication  | grep -oP 'master_repl_offset:\K\d+')
echo "replication lag bytes: $((m - s))"
```

---

*本计划书基于 2026-10-22 实测数据撰写；实施完成后请回填各阶段实际结果与偏差，归档至 `gateway/redis/`。*
