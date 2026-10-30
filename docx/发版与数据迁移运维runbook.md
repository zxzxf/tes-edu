# 发版与数据迁移运维 Runbook

> **用途**：把日常最容易踩坑的三件事——**数据库迁移怎么跑**、**发版脚本的 SSH_HOST 怎么设**、**直连后端为什么 401**——沉淀成一份可照抄的操作手册，避免每次都靠记忆或翻聊天记录。
> **适用范围**：`E:/137服务器/tes-edu` 项目，目标服务器 **137**（`root@124.221.253.137`，腾讯云，公网）、数据层 **106**（`106.12.25.5`，百度云，postgres+redis）。
> **最后更新**：2026-10-30。

---

## 0. 速查表（最常问的三件事）

| 场景 | 一句话做法 |
|---|---|
| 跑一个 SQL 迁移 | SQL 文件 `scp` 到 137 → `docker cp` 进 `edu-web-backend` → 容器内用 `asyncpg` 逐条 `execute`（**不能一条 execute 跑多语句**） |
| 发版前端/后端 | 本机跑 `SSH_HOST=root@124.221.253.137 ./deploy/publish_backend.sh web\|agent\|all`（**SSH_HOST 必须显式传**，否则连不到 137） |
| 调接口返回 401 | **别直连后端容器**，走网关 `http://gateway:8080` 或公网 `https://sd12sd.icu`——身份头（`X-User-Id` / `X-User-Role`）是网关注入的 |

---

## 1. 数据库迁移（migration）怎么跑

### 1.1 为什么不能直接 psql

- **106 的 `pg_hba` 只放行 137**（本机/容器网/137 公网 IP `124.221.253.137`），本机直连 106 不通；想在 106 上跑必须把文件送到 137 再转。
- **137 的 backend 容器里没有 `psql` 客户端**，只有 Python 3.11 + `asyncpg`。所以迁移统一用 `asyncpg` 执行 SQL。
- 数据层物理在 106，但 **137 容器内的 `DATABASE_URL` 已指向 106**（跨云公网连接，已端到端验证），所以在 137 容器里跑 `asyncpg` 就能改到 106 的库。

### 1.2 标准流程（以 `migrations/0010_xxx.sql` 为例）

```bash
# ① 本机把 SQL 送到 137（私钥 E:/137服务器/id_ed25519）
scp -i "E:/137服务器/id_ed25519" web-backend/migrations/0010_xxx.sql root@124.221.253.137:/opt/tes-edu-web/

# ② 进 137，把文件拷进 web-backend 容器
ssh -i "E:/137服务器/id_ed25519" root@124.221.253.137 \
  "docker cp /opt/tes-edu-web/0010_xxx.sql edu-web-backend:/tmp/0010_xxx.sql"

# ③ 容器内用 asyncpg 逐条执行（关键：asyncpg 一条 execute 只能跑一条语句，
#    多语句 SQL 必须先按 ";" 拆成单条，否则报错。简单做法是写个小 py 脚本读文件 split 后逐条 execute）
docker exec -i edu-web-backend python - <<'PY'
import asyncio, asyncpg, os
DSN = os.environ["DATABASE_URL"]
sql = open("/tmp/0010_xxx.sql").read()
stmts = [s.strip() for s in sql.split(";") if s.strip()]
async def main():
    c = await asyncpg.connect(DSN)
    for s in stmts:
        await c.execute(s)
    await c.close()
asyncio.run(main())
print("migration 0010 done")
PY
```

> **踩坑提醒**：
> - 不要用 `docker exec ... psql`——容器里没装。
> - 不要在一条 `await c.execute(...)` 里塞多语句——`asyncpg` 不支持，会报 `cannot insert multiple commands`。
> - 改完用 `SELECT` 抽查行数/列是否存在，确认迁移真的生效，再走下一步发版。

### 1.3 灌库（大批量数据写入）同理

批量灌 `course_kb` 等也走"本机 scp → 137 → docker cp → 容器内 python/asyncpg"路径；纯本地小改可直接在 137 容器内起 python REPL 跑 `asyncpg`。**严禁本机直连 106**（pg_hba 拒）。

---

## 2. 发版脚本的 SSH_HOST

### 2.1 开关

发版脚本 `deploy/publish_backend.sh` 默认**不内置**目标主机，必须显式传：

```bash
SSH_HOST=root@124.221.253.137 ./deploy/publish_backend.sh web    # 只发 web-backend
SSH_HOST=root@124.221.253.137 ./deploy/publish_backend.sh agent  # 只发 agent-backend
SSH_HOST=root@124.221.253.137 ./deploy/publish_backend.sh all    # 两个都发
```

- 本机私钥：`E:/137服务器/id_ed25519`（`root@124.221.253.137`）。**注意**：本机 `~/.ssh/id_ed25519` 和 25 服务器的私钥都被 137 拒绝，只有这一把能连。
- 若脚本内部 `scp`/`ssh` 不认默认 key，可在命令前追加 `SSH_OPTS="-i E:/137服务器/id_ed25519"`（视脚本实现；脚本里若已 hardcode 或读 env 则按需调整）。

### 2.2 发版后必经校验

1. 容器 healthz：`curl https://sd12sd.icu/healthz`（看 `llm`/`embed`/`vlm` 三个布尔位）。
2. 网关链路自检：经 `gateway:8080` 调一个业务接口（见 §3），确认 200 而非 401。
3. 回滚点：发版前脚本会 `docker tag <img>:real <img>:bak-YYYYMMDD`；出问题直接 `docker tag` 回退 + 换容器。

### 2.3 重建容器必须带网络别名

网关 `config.yaml` 上游写死 `http://web-backend:8000` / `http://agent-backend:8000`，而容器名是 `edu-web-backend` / `edu-agent-backend`。用 `docker run` 重建时**必须** `--network tes-edu-gateway_edu-net --network-alias web-backend`（agent 同理），否则网关 DNS `bad address`、整条链路断。

---

## 3. 直连后端为什么 401（网关身份注入）

### 3.1 现象

直接 `curl http://137内网IP:8000/api/admin/...` 或 `curl http://edu-web-backend:8000/...` 返回 **401**。

### 3.2 根因

- 后端用 `require_tenant` / `require_role` / `require_perm` 做鉴权，身份来自请求头 **`X-User-Id` / `X-User-Role` / `X-Tenant-Id`**。
- 这些头**不是客户端自己带的**，而是**网关（`gateway`）在转发时注入**的（网关先校验 JWT，再补身份头）。
- 所以绕开网关直连后端，请求没有身份头 → 401。这是设计行为，**不是 bug**。

### 3.3 正确调法

```bash
# 经网关（137 集群内）
curl -H "Authorization: Bearer <JWT>" http://gateway:8080/api/admin/exam/papers

# 或经公网（Cloudflare → 网关）
curl -H "Authorization: Bearer <JWT>" https://sd12sd.icu/api/admin/exam/papers
```

- 拿 JWT：先用 `POST /login` 换 token（账号 `admin@tes.edu` / `demo1234`，或各演示账号）。
- **联调/排障时永远经网关**，别直连后端容器——否则 401 会把你骗去查鉴权代码，其实链路没坏。

### 3.4 例外：容器间服务调用

后端服务之间（如 web-backend 调 agent-backend）走内网 `http://agent-backend:8000`，这类内部调用由调用方自行带身份头或不走鉴权，与"外部经网关"是两码事，不要混。

---

## 4. 其他常备命令

| 目的 | 命令 |
|---|---|
| 看 137 容器状态 | `ssh -i E:/137服务器/id_ed25519 root@124.221.253.137 "docker ps --format '{{.Names}}\t{{.Status}}'"` |
| 看 agent 日志（额度/降级） | `ssh ... "docker logs --tail=500 edu-agent-backend 2>&1 \| grep -iE '403\|429\|quota\|degraded'"` |
| 本地隧道连 106 库 | `ssh -N -L 15432:106.12.25.5:5432 root@124.221.253.137`（之后本机 `psql -p 15432`） |
| 跑 web-backend 单测 | 本地 `web-backend/.venv`（Python 3.11）`pytest`；容器无 psql 但可 `docker exec edu-web-backend pytest` |

---

*本 runbook 与《总任务清单与排期计划.md》配套；迁移/发版/网关相关的新坑请持续回填到此文件，避免知识只存在于聊天记录。*
