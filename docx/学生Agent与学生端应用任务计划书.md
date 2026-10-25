# 学生 Agent 与学生端应用任务计划书（上下两篇）

> **为什么补这份**：已核实 `student agent` / `学生Agent` / `学习智能体` / `学生智能体` 在仓库中 **0 命中**——该概念从未立项；而 `student-app`（安卓端）的任务**散落**在 P1 / P2 / 学生端能力扩展三份计划书中，从未独立成册。本计划书把这两块补齐。
>
> - **上篇 Part A｜学生 AI 学习智能体**：把现有"单次解题 API"升级为**伴随式、个性化的学生 Agent**。
> - **下篇 Part B｜学生端应用（student-app）**：汇总散落的 App 任务 + 补齐承载智能体所缺的端能力。
>
> **创建日期**：2026-10-25
> **现状基线（已核实）**：
> - `agent-backend` 仅有 `POST /api/app/solve`、`/solve_image`、`/similar` 三个**单次问答**接口；**无会话、无记忆、无画像、无工具调用**；无 `services/` 目录。
> - 内容安全在**网关侧**（`gateway/config.yaml` 的 `content_safety.keywords`，Redis 热更新），agent-backend 自身无过滤模块。
> - `student-app`：已有 Kotlin + Compose 暖色主题 UI、`/solve` 调用、知识图谱页（T-B5 ✅）、真机编译门禁（T-E3 ✅）；debug APK 16MB 已连生产域名 `https://sd12sd.icu/`。
> - P2 已完成能力可复用：意图识别三档路由（T-C1 ✅）、Hint 阶梯 L0–L4（T-C2 ✅）、题型路由+分步拆解（T-C3 ✅）、缓存/分级/降级（T-D1~D3 ✅）、mastery 键对齐（T-F1 ✅）。
>
> **关联文档**：`后台权限与系统集成任务计划书.md`（RBAC/§十 MCP）、`学校配置大模型BYOM能力任务计划书.md`（模型来源）、`学生端能力扩展计划书.md`（T-S0 网页端/T-F 功能开关/T-A 防疲劳）、`P2引导式对话与横向收尾任务计划书.md`、`总任务清单与排期计划.md`

---

# 上篇 · 学生 AI 学习智能体（Student Agent）

## A.1 定位：从"一次一问"到"伴随式"

| 维度 | 当前（单次解题 API） | 目标（学生 Agent） |
|---|---|---|
| 交互 | 单轮：`/solve` 一问一答 | **多轮连续会话**，上下文贯通 |
| 记忆 | 无 | **长期记忆**：薄弱点、已讲过的解法（避免重复）、偏好 |
| 个性化 | 无（人人同解） | **学情驱动**：读 mastery / ZPD / 错因分布，按人调整讲解深度 |
| 工具 | 无 | **工具调用（MCP）**：查知识图谱、错题本、组卷、相似题 |
| 模型 | 全局 env | **按租户解析**（复用 BYOM `resolve_llm_config`） |
| 端 | App 单点 | **App + 学生网页端同会话**（多端同步） |
| **组织形态** | 单体 | **多智能体（Multi-Agent）**：Orchestrator 总控 + 专能 Agent 群（诊断/讲解/出题/批改/规划/激励），详见 A.5 |

**一句话**：Student Agent = 以**学生长期学情**为底座、具备**记忆与工具**的**多轮 AI 辅导体**，而不是一次性的解题接口。

## A.2 与现有能力的关系（复用优先，不推翻）

| 能力 | 现状 | 本计划动作 |
|---|---|---|
| 意图识别 / 三档响应路由 | T-C1 ✅ 已上线 | **直接复用** |
| Hint 阶梯 L0–L4 | T-C2 ✅ 已上线 | **复用**，并接入会话上下文（同一题不重复给同一档） |
| 题型路由 + 分步拆解 | T-C3 ✅ 已上线 | **复用** |
| 缓存 / 模型分级 / 降级 | T-D1~D3 ✅ 已上线 | **复用**（注意：降级结果绝不可进缓存，已有单测锁定） |
| mastery 掌握度 / 知识图谱 | web-backend ✅（键已对齐 11/12） | **作为画像输入** |
| 步骤级错因标注 | 已有 Schema 与落地计划 | **作为错因输入** |
| 模型配置 | 全局 env | 改走 **BYOM `resolve_llm_config(tenant_id)`** |
| 内容安全 | 网关 `content_safety.keywords` | Agent 输出**过网关敏感词**；高危场景加二次校验 |
| MCP 服务 | RBAC §十（T-AM6/T-B，有效期管控） | Agent 工具调用**走 MCP**，且受有效期约束 |

## A.3 数据模型（PostgreSQL）

```sql
-- A-1) 学生 Agent 画像/配置（人设、语气、开关）
CREATE TABLE student_agent_profile (
    profile_id    VARCHAR(64) PRIMARY KEY,
    student_id    VARCHAR(64) NOT NULL,
    tenant_id     VARCHAR(64) NOT NULL,
    persona       VARCHAR(32) DEFAULT 'coach',   -- coach 教练型 / tutor 讲解型 / companion 陪伴型
    tone          VARCHAR(32) DEFAULT 'energetic',-- energetic 有激情（呼应学生端暖色 UI）/ calm
    subject_scope TEXT,                            -- 学科范围
    enabled_tools TEXT,                            -- 允许调用的 MCP 工具集
    status        SMALLINT DEFAULT 1,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- A-2) 会话（多轮 + 多端同步的关键）
CREATE TABLE student_agent_session (
    session_id   VARCHAR(64) PRIMARY KEY,
    student_id   VARCHAR(64) NOT NULL,
    tenant_id    VARCHAR(64) NOT NULL,
    platform     VARCHAR(16) NOT NULL,   -- app / web
    kp_code      VARCHAR(64),            -- 关联知识点(DAG code，遵循 mastery 键口径)
    question_id  VARCHAR(64),
    context_ptr  TEXT,                    -- 上下文指针/压缩后的摘要引用
    message_cnt  INT DEFAULT 0,
    started_at   TIMESTAMPTZ DEFAULT now(),
    last_active  TIMESTAMPTZ DEFAULT now(),
    status       VARCHAR(16) DEFAULT 'active'  -- active / closed
);

-- A-3) 长期记忆（跨会话；向量与 106 库一致 = 1024 维 qwen3.7-text-embedding）
CREATE TABLE student_agent_memory (
    memory_id    VARCHAR(64) PRIMARY KEY,
    student_id   VARCHAR(64) NOT NULL,
    tenant_id    VARCHAR(64) NOT NULL,
    memory_type  VARCHAR(32) NOT NULL,   -- weak_point 薄弱点 / taught 已讲解法 / preference 偏好 / summary 会话摘要
    content      TEXT NOT NULL,
    kp_code      VARCHAR(64),
    embedding    vector(1024),           -- 与 course_kb_embeddings 同端点、同维度
    source_session VARCHAR(64),
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_mem_student ON student_agent_memory(student_id, tenant_id, memory_type);
```

> **口径约束**：`kp_code` 一律落 DAG code（遵循 T-F1 mastery 键口径）；embedding 必须用 **ws 端点 1024 维**，否则向量空间不一致导致检索失效。

## A.4 任务计划（T-SA 系列）

| 任务 ID | 内容 | 依赖 | 估点 |
|---|---|---|---|
| **T-SA1** | 数据模型 + 迁移：`student_agent_profile` / `student_agent_session` / `student_agent_memory`（含 1024 维向量）；种子默认人设 | BYOM T-M1（可选） | 中 |
| **T-SA2** | **学情接入**：Agent 读取 mastery / ZPD / 错因分布作为上下文入参（复用 web-backend 接口，不重复造） | T-SA1, T-F1 ✅ | 中 |
| **T-SA3** | **多轮会话引擎**：把 T-C1~C3 的单次 hint 升级为连续会话（上下文窗口管理、超窗自动摘要压缩）；会话落 `student_agent_session` | T-SA1 | 高 |
| **T-SA4** | **长期记忆**：写入/检索 `student_agent_memory`（薄弱点、已讲解法去重——避免同一题重复同一讲法；会话结束自动摘要） | T-SA1, T-SA3 | 中–高 |
| **T-SA5** | **工具调用（MCP）**：Agent 可查知识图谱 / 错题本 / 组卷 / 相似题；工具走 MCP 服务实例并**受有效期约束**（过期即停用） | T-SA3, RBAC T-AM6 / T-B2 | 高 |
| **T-SA6** | **多端会话同步**：App 与学生网页端共享同一 `session_id` 与上下文（云端会话，切端不丢） | T-SA3, T-S0 | 中 |
| **T-SA7** | **安全与合规**：Agent 输出过网关 content_safety 敏感词；未成年人数据最小化；家长可见开关；与防疲劳（T-A）联动（连续学习达阈值建议休息） | T-SA3 | 中 |
| **T-SA8** | **模型来源切换**：Agent 的 LLM 改走 BYOM `resolve_llm_config(tenant_id)` + 全局 env fallback | T-SA3, BYOM T-M2 | 小–中 |
| **T-SA9** | 单测 + 评测：多轮一致性、记忆去重生效、工具调用受有效期拦截、敏感词拦截；复用 T-C5 评测口径 | T-SA2~8 | 中–高 |

## A.5 多智能体架构（Multi-Agent）

> **本节回答"Agent 是否具有多 Agent 能力"**：原设计（A.1~A.4）为**单 Agent**——一个伴随式 Agent 包办理解、讲解、出题、批改。本节把它升级为**编排 + 专能 Agent 群**。

### A.5.1 为什么需要多智能体

| 诉求 | 单 Agent 的问题 | 多 Agent 的收益 |
|---|---|---|
| 成本 | 所有任务都打同一个（大）模型 | 分档路由：诊断/批改用快模型，讲解/出题用强模型（与 T-D2 模型分级天然契合） |
| 质量 | 一个 prompt 兼顾诊断+讲解+出题，prompt 臃肿、互相干扰 | 职责单一 → prompt 短而准，可**逐个单独评测** |
| 可控 | 关掉某个能力要改代码 | 每个 Agent 可独立开关（可挂 T-F 功能开关） |
| 计费 | 无法按能力归因 | 用量按 `role_code` 归集（接入 T-D3 用量监控），可与 MCP 订阅/计费打通 |
| 演进 | 改一处牵全身 | 新增能力 = 注册一个新 Agent，不改编排主干 |

### A.5.2 角色划分

| Agent（role_code） | 职责 | 可复用现有 | 建议模型档位 |
|---|---|---|---|
| **Orchestrator** `orchestrator` | 意图判定、分派、汇总、降级 | **T-C1 意图识别三档路由 ✅** | simple（快、省） |
| **Retriever** `retriever` | RAG 供料，为其它 Agent 提供知识点/例题 | `course_kb_embeddings` ✅（1024 维） | —（非 LLM） |
| **Diagnoser** `diagnoser` | 薄弱点判定、错因归因 | 步骤级归因 Schema、mastery | hard |
| **Tutor** `tutor` | 苏格拉底引导、分步拆解、讲解 | **T-C2 Hint L0–L4 ✅、T-C3 ✅** | hard |
| **ItemGen** `itemgen` | 同类/变式题生成、组卷 | 既有组卷、MCP 工具 | hard |
| **Grader** `grader` | 判分、步骤级给分 | `judge_v2` / 判分 ✅ | simple / medium |
| **Planner** `planner` | 学习计划、复习节奏（与学生"项目" T-S 打通） | `student_project` | medium |
| **Coach** `coach` | 激励陪伴、情绪支持、与防疲劳联动 | **T-A 防疲劳 ✅**、persona 人设 | simple |

### A.5.3 协作方式（裁定：**轻多智能体**，不上重型框架）

- **不引入** AutoGen / CrewAI 等重型编排框架（运维与调试成本高、试点期不划算）。
- 采用 **"角色化 prompt + 共享状态 + 显式编排表"**：
  - Orchestrator 输出 `{intent, route: [role_code...], mode: serial|parallel}`
  - 各专能 Agent 共享同一 `session_id`、**同一份长期记忆与画像**（A-2/A-3 表），不各自为政
  - 仅 **ItemGen / Grader** 这类长流程、可独立复用的做成真正的独立服务（便于单独限流与计费）
- **降级**：任一专能 Agent 不可用/超预算/超时 → Orchestrator **降级为单 Agent 直答**（复用 T-D3 降级策略），保证学生侧永远有响应。

### A.5.4 数据模型补充

```sql
-- A-4) Agent 角色注册表（可开关、可绑模型、可计费）
CREATE TABLE student_agent_role (
    role_code     VARCHAR(32) PRIMARY KEY,   -- orchestrator/diagnoser/tutor/itemgen/grader/planner/coach/retriever
    role_name     VARCHAR(64) NOT NULL,
    duty          TEXT,                       -- 职责描述（编排表依据）
    model_grade   VARCHAR(16),                -- simple / medium / hard（沿用 T-D2 分级口径）
    enabled       BOOLEAN DEFAULT TRUE,       -- 可挂功能开关
    tenant_id     VARCHAR(64),                -- 平台默认空；租户可覆盖
    sort          INT DEFAULT 0
);

-- A-5) 编排任务流水（可观测、可归因、可计费）
CREATE TABLE student_agent_task (
    task_id      VARCHAR(64) PRIMARY KEY,
    session_id   VARCHAR(64) NOT NULL,
    role_code    VARCHAR(32) NOT NULL,
    parent_task  VARCHAR(64),                 -- Orchestrator 分派的子任务
    input_ref    TEXT,
    output_ref   TEXT,
    model        VARCHAR(64),
    tokens_in    INT, tokens_out INT,
    latency_ms   INT,
    degraded     BOOLEAN DEFAULT FALSE,       -- 是否走了降级
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_task_session ON student_agent_task(session_id);
```

### A.5.5 任务计划（T-SA10~T-SA14）

| 任务 ID | 内容 | 依赖 | 估点 |
|---|---|---|---|
| **T-SA10** | 多 Agent 角色注册与编排：`student_agent_role` + `student_agent_task` 表；Orchestrator 由"直接作答"改为**分派器**（输出 intent + route） | T-SA1, T-SA3 | 1.5d |
| **T-SA11** | 专能 Agent 实现：Diagnoser / Tutor / ItemGen / Grader / Planner / Coach 逐个接入编排（Retriever 复用现有 RAG） | T-SA10 | 2d |
| **T-SA12** | **Agent 级模型路由与成本归因**：每个 `role_code` 绑定 `model_grade`（沿用 T-D2）；用量按 role 归集到 T-D3 监控 | T-SA10, T-D2✅/T-D3✅ | 1d |
| **T-SA13** | **Agent 级开关与降级**：某 Agent 关闭/超预算/超时 → Orchestrator 降级为单 Agent 直答；开关可挂 T-F 功能开关由学校配置 | T-SA10, T-F1 | 1d |
| **T-SA14** | 多 Agent 编排评测：分派准确率、端到端延迟、成本对比（多 Agent vs 单 Agent）、降级可用性 | T-SA11~13 | 1.5d |

> **A.5 估点合计 ≈ 7 人天**；加上 A.4 的 11.75d，**上篇（学生 Agent）合计 ≈ 18.75d**，任务数 9 → **14 项**。

---

# 下篇 · 学生端应用（student-app）

## B.1 现状盘点

| 事项 | 状态 | 出处 |
|---|---|---|
| 知识图谱页（GraphScreen） | ✅ 已完成 | T-B5 / P1（T-B2/T-B3 产出） |
| 真机编译验证 | ✅ 已建 `android.yml` 门禁（test + assembleDebug） | T-E3 |
| UI 暖色主题（#FF6A3D→#F2542A→#FF3D81） | ✅ 已改并打包 | 本会话 |
| BASE_URL 指向生产 `https://sd12sd.icu/` | ✅ 已改 | 本会话 |
| 功能开关接入（按 `feature_switch` 显隐） | ⬜ 待做（承接 T-F4） | 学生端扩展 T-F4 |
| 防疲劳提醒 Dialog | ⬜ 待做（承接 T-A2） | 学生端扩展 T-A2 |
| **过程采集埋点（S4）** | ⬜ **无实证**（长期无真机 SDK） | P2 风险项 |
| **release 发布与版本管理** | ⬜ 只有 debug APK，无签名发布流程 | 缺口 |
| **Agent 连续会话 UI** | ⬜ App 现有为单次解题页 | 缺口（承接 T-SA3/T-SA6） |

## B.2 任务计划（T-AP 系列）

| 任务 ID | 内容 | 依赖 | 估点 |
|---|---|---|---|
| **T-AP1** | **Agent 连续会话 UI**：把现有单次解题页升级为多轮会话界面（消息流、打字态、引用题解/图谱）；承接 T-SA3/T-SA6 | T-SA6 | 中–高 |
| **T-AP2** | 功能开关接入：启动拉 `/api/student/features`，按集显隐底部导航与入口（承接 T-F4） | T-F4 | 中 |
| **T-AP3** | 防疲劳提醒 Dialog：前台会话计时 + 提醒弹窗（承接 T-A2） | T-A2 | 小–中 |
| **T-AP4** | **过程采集埋点（S4）**：补齐答题/停留/回看等过程数据上报，解决"长期无实证" | — | 中 |
| **T-AP5** | **发布与版本管理**：release 签名、versionCode/versionName 规范、构建产物归档、发布检查清单（当前只有 debug APK） | — | 小–中 |
| **T-AP6** | UI 规范与多机型回归：暖色组件库固化、深色/大字体/小屏适配、启动性能 | — | 中 |
| **T-AP7** | 弱网与离线：请求重试、草稿暂存、失败提示可理解 | T-AP1 | 小–中 |
| **T-AP8** | 真机全链路联调：登录 → 会话 → 解题 → 判分 → 图谱 → 周报（T-E3 只做了编译门禁，真机全链路为增强项） | T-AP1~7 | 中 |

---

# 三、与现有计划书的衔接

| 能力 | 依赖/复用 |
|---|---|
| Agent 模型来源 | **BYOM** T-M2 `resolve_llm_config(tenant_id)`（学校可配自家 key/baseUrl） |
| Agent 工具调用 | **RBAC §十** T-AM6（MCP 厂商）+ T-B2（有效期拦截，过期停用） |
| Agent 学情输入 | web-backend mastery / 知识图谱（T-F1 ✅ 键已对齐） |
| 多端承载 | **学生端扩展** T-S0（学生网页端基座）+ T-F（功能开关）+ T-A（防疲劳） |
| 引导能力底座 | **P2** T-C1~C3（已上线，直接复用） |
| 内容安全 | 网关 `content_safety.keywords`（Redis 热更新） |
| 防沉迷 | 与 T-A 防疲劳联动；未成年人合规 |
| **多 Agent 模型分档** | 复用 **P2** T-D2 模型路由分级 ✅（每个 `role_code` 绑 `model_grade`） |
| **多 Agent 用量归因** | 复用 **P2** T-D3 用量监控与预算告警 ✅（按 `role_code` 归集） |
| **多 Agent 开关** | 复用 **学生端扩展** T-F 功能开关（学校可按范围关闭某 Agent） |

**新增权限/配置**：Agent 人设与工具开关由 `student_agent_profile` 控制；**各专能 Agent 的启用与模型档位由 `student_agent_role` 控制**；平台侧 MCP 工具可用性由 RBAC §十 有效期管控。

---

# 四、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 多轮上下文膨胀导致成本/延迟飙升 | 高 | 超窗自动摘要压缩（T-SA3）；复用 T-D1 缓存 + T-D3 预算告警 |
| 记忆向量与库内向量空间不一致 → 检索失效 | 高 | 强制 **ws 端点 1024 维**（与 `course_kb_embeddings` 一致），T-SA9 校验 |
| Agent 输出不当内容（未成年用户） | 高 | 过网关 content_safety；T-SA7 二次校验 + 家长可见 |
| 工具调用绕过有效期（MCP 已过期仍调用） | 中 | T-SA5 调用前校验订阅有效期，与 T-B2 同一判定逻辑 |
| 长期记忆写错/污染画像 | 中 | `memory_type` 分类 + 来源 session 可追溯；支持按学生清除 |
| **多 Agent 编排延迟叠加**（串行调用多次 LLM，学生等待变长） | 高 | Orchestrator 优先并行可并行分支；设端到端超时上限；超时即降级单 Agent 直答（T-SA13） |
| **多 Agent 成本上升**（一次交互多次调用） | 高 | Agent 级模型分档（T-SA12）+ 按 role 归集用量；T-SA14 做成本对比，若性价比不优则回退单 Agent |
| 编排分派错误（意图判错 → 派错 Agent） | 中 | T-SA14 评测分派准确率；低置信度时降级为单 Agent 直答 |
| 多 Agent 调试/可观测性复杂 | 中 | `student_agent_task` 流水全量记录（role/model/tokens/latency/degraded），可按 session 回溯 |
| App 侧依旧无真机 → 埋点/联调无实证 | 中 | T-AP4 先补埋点契约；T-AP8 真机联调列为增强项（承接 P2 风险） |
| 只有 debug APK，无法正式发布 | 中 | T-AP5 建立 release 签名与发布流程 |

---

# 五、验收标准

**Part A（学生 Agent）**
- ✅ 同一学生连续多轮提问，Agent 保持上下文（不重复问已给条件、不重复同一讲法）。
- ✅ Agent 讲解深度随 mastery/ZPD 变化（薄弱知识点讲得更细）。
- ✅ 长期记忆生效：换会话后仍记得该生薄弱点与已讲解法。
- ✅ 工具调用走 MCP，且**订阅过期后工具不可用**（与 T-B2 一致）。
- ✅ Agent 输出命中敏感词时被拦截（网关 content_safety 生效）。
- ✅ 模型来源按租户解析（配置 BYOM 的学校走自家模型，未配置回落全局 env）。
- ✅ App 与网页端切换后会话不丢（同 `session_id`）。
- ✅ **多智能体生效**：Orchestrator 能按意图分派到 Diagnoser / Tutor / ItemGen / Grader / Planner / Coach，且各 Agent 共享同一会话与记忆。
- ✅ **Agent 级模型分档**：诊断/批改走快模型、讲解/出题走强模型，`student_agent_task` 中 `model`/`tokens` 可区分。
- ✅ **Agent 级开关与降级**：关闭某 Agent 或超预算/超时后，Orchestrator 自动降级为单 Agent 直答，学生侧始终有响应且 `degraded=true` 被记录。
- ✅ **成本可归因**：用量可按 `role_code` 归集（接入 T-D3），能回答"哪个 Agent 花了多少钱"。

**Part B（学生 App）**
- ✅ App 端为多轮会话界面（非单次问答）。
- ✅ 功能开关关闭的功能在 App 不可见；防疲劳提醒按时弹出。
- ✅ 过程采集埋点有数据落库（S4 有实证）。
- ✅ 产出**可发布的 release APK**（签名 + 版本号规范 + 发布清单）。
- ✅ 真机跑通"登录→会话→解题→判分→图谱"全链路。

---

*本计划书为新增立项文档（**v2**，上下两篇）；**v2 变更：新增 A.5 多智能体架构（Multi-Agent）**——原设计（A.1~A.4）为单 Agent，v2 升级为"Orchestrator 总控 + 8 个专能 Agent 群"，新增 `student_agent_role` / `student_agent_task` 两张表与任务 T-SA10~T-SA14（≈7 人天），上篇任务 9→14 项、估点 11.75→≈18.75d。涉及 `agent-backend`（会话引擎/记忆/MCP 工具/模型解析/多 Agent 编排）、`student-app`（会话 UI/开关/防疲劳/埋点/发布）、`web-backend`（学情接口复用）、数据库（**5 张新表**）四处改动，具体实现待 T-SA1 / T-AP1 启动时细化。T-SA1~9 与 T-AP1~8 已并入《总任务清单与排期计划》v2；**T-SA10~14 尚未并入，待确认后同步**。*
