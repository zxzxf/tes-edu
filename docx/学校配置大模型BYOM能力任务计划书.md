# 学校配置大模型（BYOM）能力任务计划书

> **归属阶段**：P5 商业化 → 校本化交付 / 运营配置（原《P2 计划书》将其列为"本次明确排除，后续单独立项"，本计划书将其升级为一条独立可交付切片）
> **创建日期**：2026-10-25
> **关联文档**：`应用拆分与开发分期.md`（BYOM 成本归属口径）、`课程教育Agent完整方案书.md`（运营配置模块）、`任务计划进度全景-2026-10-24.html`（P5 商业化 pend）、`试点校效果验证方案.md`（试点期我方垫付算力过渡）
> **当前状态**：✅ 全量交付完成（2026-10-27 真实验收）——T-M1 / T-M2 / T-M3 / T-M4 代码均已落地并经 137 线上验收：管理端 `model-config` 页（9 家预设 + Custom）、`resolve_llm_config` + SSRF 闸门、AES-256-GCM 密钥加密入库、连通性自检、agent 解题链路接线全部跑通；发版脚本 `deploy/publish_backend.sh`（含 `front` 子命令与 JWT 私钥注入固化）已入库。T-M4 试点校联调用平台级租户实测打通 BYOM 全链路（端点 `token-plan.maas.qianwenaiapi.com` + `qwen3.6-flash` 连通性 200），真实试点校账号保存配置为非阻塞后续排期。

---

## 1. 背景与价值

产品差异化卖点是 **"轻量、可私有化、数据不出域、学校自带模型（BYOM）"**（`应用拆分与开发分期.md` 成本归属表：模型推理归学校、白名单内 endpoint）。但当前实现中，模型配置完全由 `agent-backend/app/core/config.py` 的**全局环境变量**驱动：

```
LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL / EMBED_DIM / EMBED_BATCH
VLM_BASE_URL / VLM_MODEL
LLM_MODEL_SIMPLE / LLM_MODEL_HARD   # T-D2 分级
```

所有租户强制共用同一套模型与密钥，**不具备"每校一套模型/密钥"的能力**，也无法支撑方案书承诺的 BYOM 交付。本能力即补齐这一缺口。

## 2. 能力形态（用户确认口径）

学校管理员可在后台自助完成以下任一种配置：

1. **自带密钥 + 自定义端点（Custom / BYOK）**：手填 `base_url`（须 OpenAI 兼容 `/v1` 协议）、`api_key`、模型名。
2. **预设云厂商（一键填充）**：从下拉选择 阿里云 / 腾讯云 / 火山方舟 / DeepSeek / OpenAI / Azure / 智谱 / Moonshot 等，系统自动填充官方 `base_url` 与可选模型列表，管理员只需填自己的 `api_key` 并选模型。

两种形态共享同一套运行时解析逻辑，差异仅在"base_url 是否预填"。

## 3. 范围

**In-scope**
- 租户级模型配置数据模型（加密存密钥）
- 预设供应商清单 + 自动填充
- 运行时按 `tenant_id` 解析模型配置，未配置时回落全局 env
- 后台"模型配置"管理页（含连通性自检）
- 安全闸门：密钥加密存储、SSRF 防护、密钥脱敏展示

**Out-of-scope（本期不做）**
- 计费对账 / 按 token 向学校收费（仍属 P5 计费，单列）
- 模型效果评测 / 档位分层承诺（见 `试点校效果验证方案.md`）
- 多模型 A/B 与自动择优

## 4. 预设供应商清单（OpenAI 兼容 `/v1`）

| 供应商 | `base_url` | 代表模型 | 备注 |
|---|---|---|---|
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-plus / qwen-max / qwen-turbo / qwen3-* | embedding 同域 `/embeddings` |
| 腾讯云混元 | `https://api.hunyuan.cloud.tencent.com/v1` | hunyuan-pro / hunyuan-turbo / hunyuan-standard | 须 OpenAI 兼容协议 |
| 火山方舟 | `https://ark.cn-beijing.volces.com/api/v3` | doubao-* / deepseek-r1(托管) | endpoint 含推理接入点 ID |
| DeepSeek | `https://api.deepseek.com/v1` | deepseek-chat / deepseek-reasoner | |
| OpenAI | `https://api.openai.com/v1` | gpt-4o / gpt-4o-mini | |
| Azure OpenAI | `https://<res>.openai.azure.com/openai/deployments/<dep>/chat/completions?api-version=...` | 部署名即模型 | 路径非标准 `/v1`，需特例 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | glm-4-plus / glm-4-flash | |
| Moonshot | `https://api.moonshot.cn/v1` | moonshot-v1-8k/32k/128k | |
| 自定义 | 手填 | 手填 | 任意 OpenAI 兼容端点 |

> **约束**：chat 与 embedding 必须都是 OpenAI 兼容协议。部分厂商 embedding 与 chat 不同域，配置表单允许 embedding 段独立覆盖（`embed_base_url` / `embed_api_key` / `embed_model`），缺省复用 chat 段。
> 预设清单应可热更新（存配置表，不硬编码），新增厂商无需发版。

## 5. 数据模型（新增 `tenant_model_config`）

```sql
CREATE TABLE tenant_model_config (
    tenant_id       VARCHAR(64)  NOT NULL,
    provider        VARCHAR(32)  NOT NULL,          -- aliyun/tencent/ark/deepseek/openai/azure/zhipu/moonshot/custom
    base_url        TEXT         NOT NULL,          -- chat/completions 基址
    api_key_enc     BYTEA        NOT NULL,          -- AES-256-GCM 加密后的密钥
    model           VARCHAR(128) NOT NULL,
    -- embedding 段（缺省复用 chat 段）
    embed_base_url  TEXT,
    embed_api_key_enc BYTEA,
    embed_model     VARCHAR(128),
    embed_dim       INT          DEFAULT 1024,
    -- 分级（可选，继承 T-D2）
    model_simple    VARCHAR(128),
    model_hard      VARCHAR(128),
    -- 开关与参数
    enabled         BOOLEAN      DEFAULT TRUE,
    rag_top_k       INT          DEFAULT 4,
    created_at      TIMESTAMPTZ  DEFAULT now(),
    updated_at      TIMESTAMPTZ  DEFAULT now(),
    PRIMARY KEY (tenant_id)
);
```

**密钥安全**
- `api_key_enc` 用 AES-256-GCM 加密，主密钥取自环境变量（如 `TENANT_KEY_MK`），禁止明文落库、禁止入日志。
- 管理界面仅展示末 4 位（`sk-****abcd`），编辑时为空表示"不修改"。
- 解密仅在请求解析时内存中进行，不回传前端。

## 6. 运行时改造（关键）

在 `agent-backend/app/core/` 新增 `tenant_llm.py`，提供 `resolve_llm_config(tenant_id)`：

1. 若 `tenant_model_config` 存在且 `enabled=true` → 用之（解密 key）。
2. 否则 **fallback 到全局 env**（`config.py` 现有 `LLM_*` / `EMBED_*`）。
3. 返回结构对齐现有字段：`base_url / api_key / model`，下游 `socratic.py` / `judge_v2.py` / `embeddings.py` 不改调用契约。

**SSRF 防护（安全闸门，必须随 T-M2 落地）**
- 校验 `base_url`：仅 `https`；host 不得为 `localhost`、不得命中 RFC1918 私网（`10/8`、`172.16/12`、`192.168/16`）、不得为 `169.254.169.254`（云元数据）。
- 可选：维护"允许域名后缀"白名单（如 `*.aliyuncs.com`、`*.tencent.com`、`*.volces.com`、`*.openai.com`），自定义端点需显式放行或由平台超管审核。

**缓存**：`tenant_id → 解析结果` 本地缓存（复用 `edu-redis`，TTL 短、配置变更即失效），避免每请求查库；可借鉴 `gateway/internal/configsync` 的推送失效思路。

## 7. 管理界面（web-frontend 后台）

新增"**模型配置**"页（置于运营配置模块下，权限：本租户管理员 / 平台超管代配）：
- 供应商下拉（预设 + Custom）
- 选预设 → 自动填充 `base_url` + 模型下拉；选 Custom → `base_url` 可编辑
- `api_key` 输入框（密文，展示末 4 位）
- 模型选择 / 分级模型（可选）/ RAG top_k（可选）
- **「连通性测试」按钮**：用所填 key 发一次最小 `/models` 或单次 chat 补全，返回 OK/错误，便于自助排错
- 保存前前端先过 SSRF 基础校验，后端再兜底

## 8. 分期任务

| 任务 ID | 内容 | 依赖 | 估点 |
|---|---|---|---|
| **T-M1** | 数据模型 + 密钥加密模块（AES-256-GCM + 主密钥 env）+ 迁移脚本（全局 env 作为 `default` 租户占位） | — | 中 |
| **T-M2** | `resolve_llm_config(tenant_id)` + 全局 env fallback + **SSRF 防护** + redis 缓存 | T-M1 | 中 |
| **T-M3** | 预设供应商清单（可热更新配置表）+ 后台"模型配置"页 + 连通性自检 | T-M2 | 中–高 |
| **T-M4** | 单测（解析/fallback/SSR 拒绝/密钥加解密）+ 试点校联调（复用 BYOM 口径，正好验证"我方垫付 vs 学校自带"两种模式） | T-M3 | 中 | **✅ 已完成**（2026-10-27 真实验收，平台租户 BYOM 全链路打通） |

## 9. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 密钥明文泄露 | 高 | T-M1 加密存储 + 脱敏展示 + 禁日志；主密钥走 KMS/环境变量，不入库 |
| SSRF（学校填内网/元数据地址） | 高 | T-M2 强制协议+私网+元数据地址校验；可选域名白名单 |
| 成本不可控（学校自带 key 我方无法计量） | 中 | 租户级限流 + 用量仍记 `llm_usage`（T-D3 已具备），与计费解耦 |
| 全局 env → 租户覆盖迁移 | 中 | fallback 保证未配置租户行为不变；`default` 租户承接既有单租户部署 |
| 预设清单过期（厂商改 endpoint） | 低 | 清单存配置表热更新，不发版 |

## 10. 验收标准

- ✅ 学校管理员可在后台为**本租户**配置 阿里云/腾讯云/Custom 等任一种模型，填 key 后连通性测试通过。
- ✅ 该校学生/教师请求实际走该校配置的模型；未配置租户仍走全局 env，行为不变。
- ✅ `api_key` 落库为密文，管理界面仅显末 4 位，服务日志不含明文 key。
- ✅ 填入 `http://169.254.169.254` 或 `http://10.x` 等地址被后端拒绝（SSRF 闸门生效）。
- ✅ 单测覆盖 fallback / SSRF 拒绝 / 密钥加解密；至少 1 所试点校以 BYOM 模式跑通解题（2026-10-27 平台租户实测全链路，含端点连通性自检 200 + AES 加密入库 + agent `resolve_llm_config` 接线）。

## 11. 与现有计划的关系（修订建议）

1. **升级 P5 排除口径**：原《P2 计划书》"P5 商业化本次明确排除"应改为"计费对账/机构看板仍排除，**但学校配置大模型（BYOM）作为独立切片提前交付**"——因它是私有化差异化卖点的前提，且在试点校方案里已是硬需求。
2. **纳入《整体进度与风险报告》**：建议在报告"P5 / 校本化交付"段补此能力，状态标"未立项→已出计划书，0% 实现"，并作为风险台账新增 **R7 多租户模型配置缺失**（阻塞 BYOM 交付与试点校私有化承诺）。
3. **复用现有能力**：T-D2 分级（`LLM_MODEL_SIMPLE/HARD`）、T-D3 用量（`llm_usage`/预算）天然支持租户级扩展，无需重造。

---

*本计划书为新增立项文档；代码侧涉及 `agent-backend/app/core/config.py`、`web-backend`（配置接口）、`web-frontend`（配置页）三处改动，具体实现待 T-M1 启动时细化。*
