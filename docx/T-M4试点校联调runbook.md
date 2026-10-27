# W6 BYOM T-M4 试点校联调 Runbook

> 目标：至少 1 所试点校以 **BYOM 模式** 跑通解题（该校请求走该校自有模型，未配置租户回落全局 env）。
> 本文件是「真实验收」操作手册；hermetic 验证（不连库/不连外部）已通过 `scripts/tm4_pilot_integration.py`（5/5）。

## 前置就绪状态（2026-10-27 核实）

| 项 | 状态 | 说明 |
|----|------|------|
| 代码全链路 | ✅ 已提交推送 | `fd7f27c` 已到 origin/main |
| 单测 | ✅ 154 项全绿 | Python 3.11 下含 T-M4 七项 |
| hermetic 联调 | ✅ 5/5 | `scripts/tm4_pilot_integration.py` |
| 网关 X-Tenant-Id 注入 | ✅ 已自洽 | `gateway/internal/auth/auth.go:150` 无条件 Set，C 端 jwt_c 模式透传 JWT 的 tenant_id claim |
| web-backend 容器 VENDOR_CREDENTIAL_KEY | ✅ 已配置 | 加密侧可用，密钥值与 137 `.env` 一致 |
| **106 `tenant_model_config` 表** | ❌ **不存在** | 迁移脚本从未在 106 执行 |
| **137 `edu-agent-backend` 镜像** | ❌ **为 T-M4 前旧版** | 容器内无 `vendor_crypto.py`、无 `VENDOR_CREDENTIAL_KEY`，T-M3/T-M4 代码未发版 |
| agent 容器 VENDOR_CREDENTIAL_KEY | ❌ 待发版注入 | `deploy/publish_backend.sh:60` 会从 137 `.env` 注入 |

## 线上验收步骤

### Step 1 — 在 106 建表（仅首次）
经 137 跳（106 只放行 137）执行 web-backend 的迁移 SQL：
```bash
# 在 137 上，用 web-backend 容器执行迁移（复用其 DATABASE_URL）
docker cp web-backend/migrations/0001_tenant_model_config.sql edu-web-backend:/tmp/
docker exec -e DATABASE_URL=<web容器同款> edu-web-backend \
  python -c "import os,asyncpg,asyncio;from pathlib import Path;sql=Path('/tmp/0001_tenant_model_config.sql').read_text();\
  asyncio.run((lambda:asyncio.get_event_loop())(()).run_until_complete(\
  asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')).execute(sql)))"
```
更稳妥：在 137 宿主机装 `psql` 或用已有容器；验证：
```sql
SELECT count(*) FROM tenant_model_config;  -- 期望 0 行（表已建）
```

### Step 2 — 发版 agent-backend（含 web 若需）
```bash
SSH_KEY="E:/137服务器/id_ed25519" SSH_HOST=root@124.221.253.137 \
  bash deploy/publish_backend.sh agent
```
- 自动打 `:bak-YYYYMMDD` 回滚点、构建 `edu-agent-backend:real`、compose 换容器。
- 必须确认 137 `.env` 含 `VENDOR_CREDENTIAL_KEY`（与 web 容器同值），否则 agent 解密抛 RuntimeError。
- 验证：
```bash
docker exec edu-agent-backend python -c "from app.core import vendor_crypto; print('crypto OK')"
docker exec edu-agent-backend env | grep -c VENDOR_CREDENTIAL_KEY   # 期望 ≥1
docker exec edu-agent-backend python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
```

### Step 3 — 写入试点校加密配置行
通过 web-backend 后台「模型配置页」（T-M3 已落地）或直写库（需 AES-256-GCM 加密 api_key）：
```sql
-- 仅示例结构；实际密钥必须经 vendor_crypto.encrypt 加密后入库，明文不落库
INSERT INTO tenant_model_config
  (tenant_id, provider, base_url, api_key_enc, model,
   embed_base_url, embed_model, embed_dim, model_simple, model_hard, enabled, rag_top_k)
VALUES
  ('school_pilot_01', 'custom', 'https://<试点校LLM>/v1', <密文BYTES>, 'school-model',
   NULL, '', 1024, 'school-model', 'school-model', TRUE, 4);
```
密钥加密用 web 端 `app/services/vendor_crypto.py encrypt(api_key)`，或在 137 用脚本：
```bash
docker exec edu-web-backend python -c "from app.services.vendor_crypto import encrypt;import sys;print(encrypt(sys.argv[1]).hex())" "<真实key>"
```

### Step 4 — 端到端验证
```bash
# 取一个带 tenant_id=school_pilot_01 claim 的 C 端 JWT（login 拿 token）
TOKEN=$(curl -s -X POST https://<域名>/login -d '...' | jq -r .token)
# 打解题接口，网关会注入 X-Tenant-Id
curl -s https://<域名>/api/app/solve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"求解 x²-3x+2=0","mode":"explain"}' | jq '.'
```
验收判据：
- 响应正常返回解题（非降级）；
- 在 agent 容器日志 / 用量记录里确认 `model=school-model`（即该校模型，而非全局 `LLM_MODEL`）；
- 对比：用**无 tenant_id** 的 token 再打一次，确认回落全局 `LLM_MODEL`。

### Step 5 — 回滚预案
```bash
# 若出问题，换回旧镜像
ssh root@124.221.253.137 "cd /opt/tes-edu-gateway && \
  docker tag edu-agent-backend:bak-YYYYMMDD edu-agent-backend:real && \
  docker compose up -d agent-backend"
```

## 风险与待确认
1. **发版属线上改动**：Step 2 会重建 137 的 `edu-agent-backend` 容器，需人工拍板后执行。
2. **主 LLM 403 配额**：若试点校模型端点本身 403/配额耗尽，解题会降级——属外部阻塞，非代码缺陷。
3. **前端未本地构建**：本次仅后端联调；前端「模型配置页」T-M3 已落地，验收界面可另排期。
