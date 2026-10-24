# 部署编排（137 生产环境）

本目录保存 137 上的**部署编排副本**，用于重建环境可复现与变更留痕。

> ⚠️ **本目录不含任何密钥**。运行时密钥由 `env_file` 引用的 `*.env` 提供，
> 那些文件位于服务器本地、**已被 git 忽略**（仓库根目录 `.gitignore` 的 `.env` / `.env.*`）。

## 文件说明

| 文件 | 对应线上路径 | 说明 |
|---|---|---|
| `137-gateway-stack.yml` | `/opt/tes-edu-gateway/docker-compose.yml` | 网关栈：lb(nginx) + gateway×N + redis + agent-backend + web-backend + web-front |
| `137-infra-kafka.yml` | `/opt/docker-compose.yml` | 基础设施：kafka(ZK 模式) + zookeeper + kafka-init 预建 topic |
| `nginx.conf` | `/opt/tes-edu-gateway/nginx.conf` | lb 配置（静态 upstream + keepalive 长连接 + `/admin/` 前端路由） |
| `gateway-config.yaml` | `/opt/tes-edu-gateway/gateway/config.yaml` | 网关路由/限流/熔断/白名单配置 |
| `env.example` | — | 所需环境变量清单（**无值**，仅示意键名与非敏感默认） |
| `publish_backend.sh` | — | 后端发版脚本（本机 → 137）：构建 :real 镜像 + 交还 compose 换容器 |

## 首次部署步骤（137）

```bash
# 1) 目录与源码
mkdir -p /opt/tes-edu-gateway/gateway /opt/tes-edu-agent /opt/tes-edu-web /opt/tes-edu-gateway/redis
cp 137-gateway-stack.yml /opt/tes-edu-gateway/docker-compose.yml
cp nginx.conf            /opt/tes-edu-gateway/nginx.conf
cp gateway-config.yaml   /opt/tes-edu-gateway/gateway/config.yaml
cp 137-infra-kafka.yml   /opt/docker-compose.yml

# 2) 镜像来源（2026-10-23 起统一走 ACR，消除 G6 双描述）
#    三个业务镜像由 CI 自动构建并推送：.github/workflows/build-images.yml
#    （需仓库 Secrets：ACR_USERNAME / ACR_PASSWORD）。
#    如需本地手动构建等价镜像：
#      docker build -t registry.cn-shanghai.aliyuncs.com/mis_ser/edu-gateway:latest      gateway
#      docker build -t registry.cn-shanghai.aliyuncs.com/mis_ser/edu-agent-backend:latest agent-backend
#      docker build -t registry.cn-shanghai.aliyuncs.com/mis_ser/edu-web-backend:latest   web-backend
#    compose 默认拉取 :latest；要锁定版本可设 IMAGE_TAG=sha-<commit> 后 up。

# 3) 基础设施（kafka/zk）先起，再起网关栈
cd /opt && docker compose up -d
cd /opt/tes-edu-gateway && docker compose -p tes-edu-gateway up -d --scale gateway=2
```

## 日常发版（本地构建通道，2026-10-24 起）

CI（ACR）通道就绪前（仓库 Secrets `ACR_USERNAME` / `ACR_PASSWORD` 缺失时
`build-images.yml` 全部在登录步骤失败），线上发版走本地构建：

```bash
# 后端（web / agent / all）：构建 :real 镜像 → compose 换容器 → 健康检查
SSH_KEY=<本机私钥> SSH_HOST=137 bash deploy/publish_backend.sh all

# 网关（路由/config.yaml 变更时）：同步源码 → 本地构建 → 滚动替换双副本
rsync/tar 同步 gateway/ → 137:/opt/tes-edu-gateway/gateway/
ssh 137 'cd /opt/tes-edu-gateway && ./start.sh build'   # 自动 HUP nginx

# 前端 web-front（web-frontend/ 变更时）：同步源码 → 构建 → compose 换容器
# 注意：前端挂在 /admin 子路径（vite base + router base 均为 /admin/），
# lb 的 location /admin/ 剥前缀反代到 edu-web-front 容器。
tar 同步 web-frontend/ → 137:/opt/tes-edu-web-front/
ssh 137 'docker build -t edu-web-front:real /opt/tes-edu-web-front \
  && cd /opt/tes-edu-gateway \
  && JWT_PUBLIC_KEY="$(cat secrets/jwt_public.pem)" \
     JWT_PRIVATE_KEY="$(cat secrets/jwt_private.pem)" \
     REDIS_PASSWORD="$(sed -n "s/^REDIS_PASSWORD=//p" .env)" \
     docker compose up -d web-front \
  && docker kill -s HUP edu-lb'   # 容器重建后 IP 变化，必须 HUP 刷新 lb 上游
```

> ⚠️ **lb 的 `/admin/` 上游是启动时一次性解析**（与 gateway_pool 同样的局限）：
> `edu-web-front` 容器**重建/换 IP 后必须 `docker kill -s HUP edu-lb`**，
> 否则请求会打到回收后分配给别的容器的旧 IP 上（表现为 FastAPI 404）。

> 密钥均由 137 本地文件读入（`secrets/jwt_*.pem`、`.env`、`*.env`），不在本机落地。
> Secrets 就绪后切回 ACR：改 `137-gateway-stack.yml` 三个 `image:` 为
> `registry.cn-shanghai.aliyuncs.com/mis_ser/edu-<svc>:${IMAGE_TAG:-latest}`，
> 删去 gateway 的 `build:` 段，本脚本与 start.sh build 退役。

## 环境变量

复制 `env.example` 到对应位置并填值，**权限设为 600**：

```bash
cp env.example /opt/tes-edu-gateway/.env            # 网关栈：REDIS_PASSWORD / JWT_PUBLIC_KEY
# agent-backend.env / web-backend.env 见各服务目录下的 .env.example
chmod 600 /opt/tes-edu-gateway/.env *.env
```

关键变量（详见 `env.example`）：
- 网关栈：`REDIS_PASSWORD`、`JWT_PUBLIC_KEY`（RSA 公钥 PEM）、`JWT_PRIVATE_KEY`（RSA 私钥 PEM，web-backend 的 `/login` 签发用，多行 PEM 经 compose 变量插值注入）——**未设置会启动失败**（compose 用 `${VAR:?}` 强制）
- agent-backend：`LLM_*`、`EMBED_*`、`DB_DSN`、`KAFKA_BOOTSTRAP`
- web-backend：`DATABASE_URL`、`KAFKA_BOOTSTRAP`

## 关键约束（踩过的坑）

1. **web-backend 必须监听 8000**：网关 `config.yaml` 上游写死 `http://web-backend:8000`，端口不一致会导致 `/api/admin/*` 全部转发失败。
2. **网关不做路径改写**：路由按前缀匹配后**原样转发**。web-backend 的 `/login`、`/learning/*` 必须挂在根路径（`main.py` 双挂载：`/api/admin` 给 B 端、根路径给网关 `/login` 与 `/learning/` 路由）——2026-10-24 部署时只挂 `/api/admin` 导致网关侧 404，已由 `tests/test_route_mounts.py` 锁定契约。
3. **Kafka 网络**：kafka/zk 原本只在 `opt_default`，与网关栈的 `edu-net` 不通，容器名解析不到；已在 infra 编排中声明 `external: true` 的 `tes-edu-gateway_edu-net` 并接入，两个网络并存。
4. **106 的 pgvector 只允许 137 连接**：`pg_hba.conf` 白名单仅放行 `124.221.253.137`；DSN 中的密码含 `@`/`#` 必须百分号编码（`%40`/`%23`）。
5. **Redis 主从**：137 为主、106 为从，见 `gateway/redis/` 下的计划与监控脚本。
6. **容器归 compose 管理**：勿再用 `docker run` 直接起 `edu-*-backend`（会与 compose 争抢容器名，`up` 报 Conflict）；换装一律交还 compose。
7. **web-front 必须显式挂 edu-net**：compose 服务不写 `networks: - edu-net` 会落到项目默认网络 `tes-edu-gateway_default`，与 gateway/lb 不同网——其 nginx 的 `gateway` 上游启动解析即 `[emerg] host not found in upstream` 崩溃循环（2026-10-24 T-E1 踩坑）。容器内 `/api` 反代已同时改为 `resolver 127.0.0.11 + 变量 proxy_pass`，对网关重建免疫。
8. **B 端登录 token 必须带 tenant_id**：网关 `/api/admin/*` 路由 `require_tenant: true`，JWT 无 `tenant_id` claim 一律 403（管理后台能打开但全部数据接口被挡）。`create_token(user_id, role, tenant_id=...)` 已支持；C 端 student 不带（`/learning/*` 为 jwt_c 路由，不要求租户）。

## Kafka 模式决策：ZK → KRaft（T-E2 评估结论，2026-10-25）

**结论：试点开跑前维持 ZK 模式；KRaft 升级列为「试点后第一项工程任务」。**
评估与实施资产均已就位，切换只需一条命令（见下）。

### 现状基线（2026-10-25 实测）

| 项 | 值 |
|---|---|
| 镜像 | `mis_ser/kafka:3.8.0`（jar 确为 3.8.0）+ `zookeeper:3.9` |
| 业务 topic | `course_kb_ingest` / `question_parsed` / `kg_build` / `student_event` / `drill_sync`（各 1 分区，RF=1） |
| 消费者组 | `student-event-consumer`（student_event，21 条，**lag 0**）、`course-kb-ingest`（course_kb_ingest，0 条，lag 0） |
| 事件量 | 全集群累计 21 条，无堆积 |

### 已完成的评估与旁路实测（不动生产）

在 137 上用**生产同款镜像**起独立 KRaft 集群（`kraft-probe`，测完即删，ZK 集群未受影响），逐项验证：

1. 镜像支持 KRaft：`kafka-storage.sh format` 可格式化、broker+controller 混合角色正常启动、探活通过。
2. topic 预建：5 个业务 topic 全部 `Created`。
3. 生产/消费往返：3 条消息 3 条收到，零丢失。
4. 消费者组协调器：`--describe` 正常，offset 可重建（迁移后无需搬运历史 offset）。
5. 应用侧零改造：`edu-agent-backend` 容器内 **aiokafka 直连 KRaft** 收发成功（produce/consume 均 OK）。

### 为什么现在不切

- 试点（T5）尚未开跑，事件量 21 条、lag 恒 0，**ZK 模式的规模化隐患在当下完全没有兑现**；
- 迁移收益是"规模化后的运维简化"，而代价是"把试点期的链路稳定性押在一次非必要变更上"；
- 因此按计划书备选路径：书面化维持决策 + 交付已验证的实施资产，试点后第一周执行。

### 试点后怎么切（一条命令）

```bash
# 1) 体检 + 打印计划（默认 dry-run，会校验所有消费者组 lag 必须为 0）
SSH_KEY=~/.ssh/id_ed25519 SSH_HOST=137 bash deploy/kafka_kraft_migrate.sh

# 2) 执行迁移（停后端 → 停 ZK 集群 → 起 KRaft → 预建 topic → 起后端 → 对账 → healthz）
SSH_KEY=~/.ssh/id_ed25519 SSH_HOST=137 bash deploy/kafka_kraft_migrate.sh --execute

# 3) 出问题就回滚到 ZK 模式（旧数据卷保留）
SSH_KEY=~/.ssh/id_ed25519 SSH_HOST=137 bash deploy/kafka_kraft_migrate.sh --execute --rollback
```

迁移要点：**不搬运历史消息**（要求切流前 lag=0，脚本强制校验），KRaft 用独立数据卷
`/var/lib/kafka-kraft`，因此不存在"重复消费旧事件"的风险；观察一周无异常后
`docker rm -f zookeeper` 下线 ZK。编排文件：`deploy/137-infra-kafka-kraft.yml`
（已同步到 137 `/opt/tes-edu-gateway/`，`docker compose config` 零告警）。

### 已记录的坑（下次执行前先读）

1. **镜像 bin 脚本与 jar 版本不一致**：`kafka-storage.sh` 是 3.5- 签名，**不接受 `--standalone`**，
   直接调用会 `unrecognized arguments` 而启动失败。脚本已做双签名兼容（先无参、再 `--standalone`）。
2. **compose 会插值脚本里的 `$VAR`**：KRaft 编排的 `command` 里所有 shell 变量必须双写美元符
   （同 ZK 版 `$$cf`），否则启动时被替换成空串。
3. **新集群 coordinator 冷启动**：首次消费可能短暂 `GroupCoordinatorNotAvailableError`
   （`__consumer_offsets` 建 topic 中），aiokafka 会自动重试成功——迁移后消费者启动有几秒抖动属正常。

## 回滚

服务器上每次修改 compose 前会留备份 `docker-compose.yml.bak.<时间戳>`。
回滚方式：改回备份内容后 `docker compose -p tes-edu-gateway up -d <service>`；
若要把后端退回 mock，把 `image:` 改回 `registry.cn-shanghai.aliyuncs.com/mis_ser/edu-mock-backend:latest` 再重建。

## 更新本目录

线上编排改动后，请把最新文件同步回本目录并提交，保持"线上状态 = 仓库记录"。
