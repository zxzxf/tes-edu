# W6 BYOM T-M4 试点校联调 Runbook

> 目标：至少 1 所试点校以 **BYOM 模式** 跑通解题（该校请求走该校自有模型，未配置租户回落全局 env）。
> 本文件是「真实验收」操作手册；hermetic 验证（不连库/不连外部）已通过 `scripts/tm4_pilot_integration.py`（5/5）。

## 一、验收结论（2026-10-27 已完成 ✅）

整条 BYOM 链路在 137 线上环境**已完整闭环并通过真实验收**：

| 验收项 | 结果 | 说明 |
|--------|------|------|
| 登录签发 admin+tenant JWT | ✅ | `JWT_PRIVATE_KEY` 私钥已注入 web-backend 容器 |
| `GET /api/admin/byom/presets` | ✅ 返回 9 家预设厂商 | 前端下拉框数据来源（此前缺失导致"厂商id不存在"） |
| `POST /api/admin/byom/connectivity-test` | ✅ `ok:True, HTTP 200` | 用 `token-plan.maas.qianwenaiapi.com` + key + `qwen3.6-flash` 实测通过 |
| `PUT /api/admin/byom/config` | ✅ 保存成功 | 密钥 AES-256-GCM 加密入库，回显仅末 4 位 `****cFxs` |
| 前端 `model-config` 页 | ✅ BYOM JS 已进入新 bundle | `edu-web-front:real` 重建含 `ModelConfig.vue` + `api/byom.js` |
| agent 解题链路 | ✅ `resolve_llm_config` 已接线 | tenant_id → 该校 BYOM 配置，缺省回落全局 `LLM_MODEL` |

外部访问入口：`https://sd12sd.icu/admin/system/model-config`（经 cloudflared 隧道 → edu-lb → `/admin/` 静态页；API 经 `location /` → 网关 `/api/admin/*` → web-backend）。

### 你最初报"报错厂商id不存在"的根因与修复

| 问题 | 根因 | 修复 |
|------|------|------|
| 厂商列表拿不到 → "厂商id不存在" | 旧 web-backend 镜像（10-25）根本没有 BYOM 路由代码（404） | 重新 build 并部署含 BYOM 的 `edu-web-backend:real` |
| 登录后所有 `/api/admin/*` 被 401/403 | `docker run` 重建时漏注入 RS256 私钥 `JWT_PRIVATE_KEY`（compose 靠宿主 `.env` 注入，run 方式未带） | 重建容器时用 `-e "JWT_PRIVATE_KEY=$(cat secrets/jwt_private.pem)"` 注入完整私钥 |
| 前端 `model-config` 页是旧代码 | 137 的 `edu-web-front` 从未部署含 `ModelConfig.vue` 的新前端 | 同步本机前端源码、rebuild 并重建 `edu-web-front:real` |

---

## 二、发版流程（已固化到脚本，勿再手敲 docker run）

> 所有发版统一走 `deploy/publish_backend.sh`，已固化 T-M4 踩过的全部坑。

**关键固化点（每条都是踩过的坑）**：
1. 弃用 `docker compose up` 换容器 —— 多行 PEM 的 `JWT_PRIVATE_KEY` 无法被 compose 变量插值正确注入，会漏私钥导致登录签不出 token。改为 `docker run -e "JWT_PRIVATE_KEY=$(cat secrets/jwt_private.pem)"` 精确注入。
2. 网络名写死 `tes-edu-gateway_edu-net`（compose 项目名前缀），不是 docker-compose.yml 里的相对名 `edu-net`。
3. 强制带网络别名 `--network-alias web-backend` / `agent-backend`（网关 config.yaml 上游写死这两个别名，漏了就 DNS `bad address`、整条链路断）。
4. 新增 `front` 子命令：前端源码同步 + rebuild + 重建 nginx 容器（BYOM 的 `ModelConfig.vue` 必须进 bundle）。
5. 换装前自动打 `:bak-YYYYMMDD` 镜像标签留回滚点；重建后跑 healthz + 经网关的 BYOM 端到端自检。

**用法**：
```bash
SSH_KEY="E:/137服务器/id_ed25519" SSH_HOST=root@124.221.253.137 \
  bash deploy/publish_backend.sh web|agent|front|all
```
- `web`：同步 web-backend 源码 → build `edu-web-backend:real` → 重建容器（注入私钥+env-file+别名）→ 网关 BYOM 自检。
- `agent`：同步 agent-backend 源码 → build `edu-agent-backend:real` → 重建容器（注入 `--env-file agent-backend.env`，含 `VENDOR_CREDENTIAL_KEY`）。
- `front`：同步 web-frontend 源码 → build `edu-web-front:real` → 重建 nginx 容器。
- `all`：按 web → agent → front 顺序全量重建，每项重建后做 healthz + BYOM 自检。

> 注：`deploy/publish_backend.sh` 本身**不含任何密钥明文**——私钥/密钥仅以 `secrets/*.pem`、`.env` 路径引用，运行时从 137 读取，符合"提交必须排除密钥"约定。

---

## 三、首次建表步骤（仅 106 首次，已执行）

经 137 跳（106 只放行 137）执行迁移：
```bash
# 在 137 上，用 web-backend 容器执行迁移（复用其 DATABASE_URL）
docker cp web-backend/migrations/0001_tenant_model_config.sql edu-web-backend:/tmp/
docker exec -e DATABASE_URL=<web容器同款> edu-web-backend \
  python -c "import os,asyncpg,asyncio;from pathlib import Path;sql=Path('/tmp/0001_tenant_model_config.sql').read_text();\
  asyncio.run((lambda:asyncio.get_event_loop())(()).run_until_complete(\
  asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')).execute(sql)))"
```
`vendor_preset` 表由 web-backend `main.py` 启动时自动 `seed_presets(db)` 播种 9 家（阿里云/腾讯云/火山/DeepSeek/OpenAI/Azure/智谱/Moonshot/自定义）。

---

## 四、试点校真实验收（后续排期，非阻塞）

当前验收用平台级 `platform` 租户（admin 登录）写了一条测试配置，证明全链路通。
试点校真实验收时：
1. 让试点校管理员账号（带真实 `tenant_id`）登录 `https://sd12sd.icu/admin/system/model-config`。
2. 选「自定义 / Custom」，填端点 URL（与 Key **分两个框**），模型名（如 `qwen3.6-flash`），点连通性自检 → 保存。
3. 用该校 C 端 JWT 打 `/api/app/solve`，网关注入 `X-Tenant-Id` → agent `resolve_llm_config` 走该校配置。
4. 验收判据：响应正常返回解题；agent 日志确认 `model=<该校模型>`；用无 tenant_id 的 token 再打一次，确认回落全局 `LLM_MODEL`。

### 回滚预案
```bash
# 若出问题，换回旧镜像（发布时已打 :bak-YYYYMMDD）
ssh root@124.221.253.137 "docker tag edu-web-backend:bak-YYYYMMDD edu-web-backend:real && \
  docker run -d --name edu-web-backend --network tes-edu-gateway_edu-net \
  --network-alias web-backend --network-alias edu-web-backend --restart unless-stopped \
  --env-file /opt/tes-edu-gateway/web-backend.env \
  -e \"\$(cat /opt/tes-edu-gateway/secrets/jwt_private.pem | sed 's/^/JWT_PRIVATE_KEY=/')\" \
  edu-web-backend:real uvicorn main:app --host 0.0.0.0 --port 8000"
```

---

## 五、风险与待确认

1. **主 LLM 403 配额**：若试点校模型端点本身 403/配额耗尽，解题会降级——属外部阻塞，非代码缺陷。
2. **前端未本地构建**：`model-config` 页已随 `front` 子命令部署到 137，验收界面可用。
3. **平台级测试配置**：当前 `platform` 租户下有一条 BYOM 测试配置（端点 `token-plan.maas.qianwenaiapi.com` + `qwen3.6-flash`），如不需要可经后台删除。
