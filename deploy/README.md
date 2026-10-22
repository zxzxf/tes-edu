# 部署编排（137 生产环境）

本目录保存 137 上的**部署编排副本**，用于重建环境可复现与变更留痕。

> ⚠️ **本目录不含任何密钥**。运行时密钥由 `env_file` 引用的 `*.env` 提供，
> 那些文件位于服务器本地、**已被 git 忽略**（仓库根目录 `.gitignore` 的 `.env` / `.env.*`）。

## 文件说明

| 文件 | 对应线上路径 | 说明 |
|---|---|---|
| `137-gateway-stack.yml` | `/opt/tes-edu-gateway/docker-compose.yml` | 网关栈：lb(nginx) + gateway×N + redis + agent-backend + web-backend |
| `137-infra-kafka.yml` | `/opt/docker-compose.yml` | 基础设施：kafka(ZK 模式) + zookeeper + kafka-init 预建 topic |
| `nginx.conf` | `/opt/tes-edu-gateway/nginx.conf` | lb 配置（静态 upstream + keepalive 长连接） |
| `gateway-config.yaml` | `/opt/tes-edu-gateway/gateway/config.yaml` | 网关路由/限流/熔断/白名单配置 |
| `env.example` | — | 所需环境变量清单（**无值**，仅示意键名与非敏感默认） |

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

## 环境变量

复制 `env.example` 到对应位置并填值，**权限设为 600**：

```bash
cp env.example /opt/tes-edu-gateway/.env            # 网关栈：REDIS_PASSWORD / JWT_PUBLIC_KEY
# agent-backend.env / web-backend.env 见各服务目录下的 .env.example
chmod 600 /opt/tes-edu-gateway/.env *.env
```

关键变量（详见 `env.example`）：
- 网关栈：`REDIS_PASSWORD`、`JWT_PUBLIC_KEY`（RSA 公钥 PEM）——**未设置会启动失败**（compose 用 `${VAR:?}` 强制）
- agent-backend：`LLM_*`、`EMBED_*`、`DB_DSN`、`KAFKA_BOOTSTRAP`
- web-backend：`DATABASE_URL`、`KAFKA_BOOTSTRAP`

## 关键约束（踩过的坑）

1. **web-backend 必须监听 8000**：网关 `config.yaml` 上游写死 `http://web-backend:8000`，端口不一致会导致 `/api/admin/*` 全部转发失败。
2. **Kafka 网络**：kafka/zk 原本只在 `opt_default`，与网关栈的 `edu-net` 不通，容器名解析不到；已在 infra 编排中声明 `external: true` 的 `tes-edu-gateway_edu-net` 并接入，两个网络并存。
3. **106 的 pgvector 只允许 137 连接**：`pg_hba.conf` 白名单仅放行 `124.221.253.137`；DSN 中的密码含 `@`/`#` 必须百分号编码（`%40`/`%23`）。
4. **Redis 主从**：137 为主、106 为从，见 `gateway/redis/` 下的计划与监控脚本。

## 回滚

服务器上每次修改 compose 前会留备份 `docker-compose.yml.bak.<时间戳>`。
回滚方式：改回备份内容后 `docker compose -p tes-edu-gateway up -d <service>`；
若要把后端退回 mock，把 `image:` 改回 `registry.cn-shanghai.aliyuncs.com/mis_ser/edu-mock-backend:latest` 再重建。

## 更新本目录

线上编排改动后，请把最新文件同步回本目录并提交，保持"线上状态 = 仓库记录"。
