# 课程教育 Agent 系统 —— 任务路线图

> 本文件是项目剩余工作的可视路线图，与对话中的任务清单（TaskList）保持同步。
> 更新时间：2026-10-23

## 优先级排序（按"杠杆最高、阻塞最少"）

| 顺位 | 项目 | 任务号 | 状态 |
|---|---|---|---|
| ① | 真实题库灌 3000 题 | #23 | ✅ 已确认灌库（106 生产库，2026-10-23 连库核验） |
| ② | 测试 / CI | #19 | ✅ 完成 |
| ③ | AGE 图库常驻 | #20 | ⬜ 待办 |
| ④ | **P4 家长端** | | |
| ④·核心闭环 | 学情周报 + 薄弱点 + 报告页 | #21 | ✅ 完成（第一刀） |
| ④·安全护栏 | 内容拦截 + 家长告警 | #24 | ⬜ 待办 |
| ④·绑定隔离 | 家长↔学生绑定 + 租户隔离 | #25 | ⬜ 待办（上线前必做） |
| ④·语音 | ASR / TTS 语音交互 | #26 | ⬜ 待办 |
| ④·志愿 | 新高考志愿填报（河南 3+1+2） | #27 | ⬜ 待办 |
| ④·数字人 | 数字人讲题短视频 | #28 | ⬜ 待办 |
| ⑤ | P5 商业化 | #22 | ⬜ 待办 |

## 排序理由

- **① 真实题库灌 3000 题**：隐藏地基。没有题 + 知识点本体，RAG / 举一反三 / 组卷 / 自适应全空转。
  工程链路（`agent-backend/scripts/import_bank.py`）已在 45 题验证，扩量主要是内容获取 + 逐题标注 + 跑脚本，
  **技术风险低、杠杆最高**。瓶颈是内容来源（用户选"你提供真题/采购库"），故暂停等素材。
- **② 测试 / CI**：唯一完全不阻塞的横向工程，且能保护现有真实系统。已与 ① 并行完成。
- **③ AGE 图库常驻**：差异化核心（图谱驱动 vs 题库驱动）。当前掌握度已用 PostgreSQL 关系表算出真实值（mastery=0.5），
  AGE 为增强非阻塞，且依赖 ① 的知识点 DAG（≥300 点）先就绪，故排 ④ 之后更顺。
- **④ P4 家长端**：产品面，直接消费既有掌握度数据，可与 ③ 并行。
- **⑤ P5 商业化**：多租户 / 计费 / To B，依赖前面全部数据资产 + 家长端。

## ① 真实题库灌 3000 题（✅ 已确认灌库，2026-10-23 连 106 核验）

- 工程流水线 `import_bank.py` 已就绪（commit `e6276db`），支持 csv/json/jsonl/xlsx、中英文列名自动映射、
  知识点按 `；、/|,，` 自动拆分、批量 embedding（分批 + 指数退避重试 + 可重跑）、`content_hash` 幂等去重、
  **双写** `course_kb`（Web 规范库）+ `course_kb_embeddings`（Agent RAG 检索表）、`--dry-run` 不连库校验。
- 已用 2025 新高考I卷公开真题 7 题做 dry-run 验证（commit `e6276db`）。
- **远端新增（7cb867f→e60361d，11 提交 / 297 文件 / +63716 行）**：
  - 真题素材：`真题素材/gaokao-system/` 历年真题干货（1952–2026 年 xlsx/docc，含数学 / 语文等）。
  - 知识点本体：knowledge_points 全量 JSON + `build_knowledge_dag.py` + knowledge_dag JSON/MD（人教A版高中数学）。
  - 步骤级归因：全题型落地，`course_kb_steps` **674 步、213 题 100% 覆盖**；`p0_attribution_maps.sql` 迁移
    （distractor_map / wrong_value_map / typical_errors + student_step_errors）；`gen_attribution_maps.py` 内容生成器。
  - 配套文档：步骤级归因落地计划书 / 标注 Schema v1 / 真题校验报告；修正河南新高考首考年份 2026→2025。
  - `.env.example` 纳入版本管理；隔离 seed demo 假题、统一题型 / 学科口径、补后端发版脚本。

### 连库核验结果（2026-10-23，SSH 隧道 137→106:5432，DSN `postgresql://tes:****@106.12.25.5:5432/tes_edu`）

| 表 | 行数 | 说明 |
|---|---|---|
| `course_kb` | **213** | 真实题库（数学 123 + 物理 90；卷别：新高考I卷/河南卷·物理/新课标卷·理综物理/全国乙卷·理综物理），`content_hash` 唯一约束在位 |
| `course_kb_steps` | **674** | 步骤级归因，含 `distractor_map`/`wrong_value_map`/`typical_errors` 列，样本为真实每步判分 + 错因 |
| `course_kb_embeddings` | **259** | RAG 向量（213 题全覆盖，部分题多段向量），**1024 维**（= qwen text-embedding-v4），HNSW 余弦索引 `idx_kb_embeddings_vec` 在位 |
| `kb_knowledge_points` | **435** | 知识点本体（code/章节/重要度/教材/理解·记忆·类比 教学法字段） |
| `kb_kp_prerequisites` | **435** | 知识图谱 DAG 边（kp_code→prereq_code，relation_type=prerequisite） |
| `student_mastery` | 12 | 真实学情（tenant_id=henan_01，掌握度按 correct/(correct+wrong) 计算） |
| `student_events` | 4 | 真实学情事件流水 |
| `student_step_errors` | **0** | ⚠️ 步骤级错因归因**数据未灌**（迁移 SQL `p0_attribution_maps.sql` 在仓库内，但生产库为空） |
| `_bak_steps_20261023` | 43 | 早期 steps 备份（2026-10-23），已被 674 步版取代 |

- **结论**：题库 / 步骤归因 / RAG 向量 / 知识点本体 / 知识图谱 DAG **均已真灌入 106 生产库**，链路可用（向量索引、`content_hash` 去重均在位）。
- **遗留 1**：`student_step_errors` 为空——步骤级错因归因数据（distractor/wrong_value/typical_errors 的"学生侧"落地）未导入，目前仅 `course_kb_steps` 自带错因。若需"错因→薄弱点"闭环需补灌。
- **遗留 2（建议复测）**：召回率尚未用 `eval_retrieval.py` 实测（仅验了向量维度与索引存在）。建议跑一次确认 ≥85%，并视结果决定是否需重新 embedding 或调 HNSW 参数。
- 新分支 `feature/redis-master-slave`（Redis 主从），与 deploy/Redis 相关，③ 与部署编排可关注。

## ② 测试 / CI（✅ 完成）

- `agent-backend/tests/test_import_bank.py`：6 用例，纯 stdlib 不连库。
- `web-backend/tests/test_core.py`：12 用例 + `test_reports.py` 3 用例，纯逻辑不连库。
- `.github/workflows/ci.yml`：test-agent / test-web 双 job。
- `.github/workflows/build-images.yml`：CI 构建并推送 gateway / agent-backend / web-backend 三镜像到阿里云 ACR。
- `deploy/137-gateway-stack.yml`：移除本地 `build:`、三服务统一 `image: ACR/${IMAGE_TAG:-latest}`，消除 G6 镜像双描述。
- 提交：`367a8a6`、`1a996ce`。本地验证 agent 6 passed / web 15 passed。

## ③ AGE 图库常驻（⬜ 待办）

- Apache AGE 容器常驻 edu-net；基于知识点 DAG（≥300 点）建图谱，增强 ZPD / 前置回溯自适应。
- 依赖 ① 的知识点本体先就绪；当前不在阻塞路径上（关系表兜底已能算掌握度）。

## ④ P4 家长端

### ④·核心闭环（✅ 完成，第一刀）

- `app/services/reports.py`（新增）：`compute_weekly_report` 连库查 `student_mastery` + 本周 `student_events`；
  `_build_report` 纯函数推导薄弱点（掌握度 <0.6，升序）、平均掌握度、已掌握数、学习投入（活跃天数 + 练习次数 + 估算时长）、家长建议。
- `app/api/reports.py`：`/reports/weekly` 由写死桩改为真实计算；`/reports/weekly/page` 返回自包含 HTML 报告页（内联 CSS、无外部依赖、HTML 转义）。
- 提交：`91949c7`。本地 web 15 passed。

### ④·安全护栏（⬜ 待办，#24）

- 敏感内容拦截 + 家长告警。轻量版可用关键词 / 规则拦截（不需大模型），产出家长告警接口 / 通知。
- 依赖 P3 错题本 / 讲解文案成熟；与第一刀核心闭环松耦合。

### ④·家长↔学生绑定 + 租户隔离（⬜ 待办，#25，上线前必做）

- 当前 `/reports/weekly` 按 `student_id` 检索，无"家长↔学生绑定表"，租户隔离仅作可选查询参数。
- 需建绑定表 + 在 report 层强制 parent 只能看自己绑定学生的数据、强制 tenant_id 隔离，再正式开放家长端。

### ④·ASR / TTS 语音交互（⬜ 待办，#26）

- 依赖本地语音模型（CosyVoice / ChatTTS 等）就绪，属独立基建线。

### ④·新高考志愿填报（⬜ 待办，#27）

- 河南 2025 首考新高考（3+1+2 首年），2026 为第二年，过渡期规则变化大、本地刚需。规则引擎产品，可独立成线。

### ④·数字人讲题短视频（⬜ 待办，#28）

- 讲解文案 → TTS（本地）→ 数字人驱动 → ffmpeg 合成 → MinIO + Kafka 异步生成。
- 依赖 P3 讲解文案成熟 + 数字人管线，靠后。

## ⑤ P5 商业化（⬜ 待办）

- 多租户 / 计费 / To B 交付，依赖前面全部数据资产 + 家长端。
- 合同三条硬要求（白名单 / 心跳 / 指标回传）网关侧已落地，剩机构隔离与计费。
- **决策（2026-10-26）**：移除「微信/支付宝企业商户资质」外部阻塞项（计划书 v4.4 已同步删除相关 5 处引用）。
  - 原因：当前为个人域名备案、无营业执照，原生微信/支付宝商户无法申请，不应作为开发阻塞。
  - 落地口径：T-B 商业化系列（T-B1~T-B6）保留，但支付接入走**沙箱优先**路径——T-B3 下单 / T-B4 回调按沙箱实现，暂不接真实收款；真实商户资质待试点校合作时由学校主体代申，或改用第三方聚合支付。
  - 代码侧含义：`/api/pay/notify/` 等按沙箱契约实现，勿假设已具备真实商户号 / 证书。

## 推荐后续推进顺序

③ AGE 常驻（依赖 ① 知识点 DAG，现已具备）→ ④ 剩余子任务（建议先做 #25 绑定隔离，再 #24 安全护栏）→ ⑤ P5；
① 已在远端大幅推进，重点转为"确认 / 完成灌库到 106 + 复测召回率"，可并行穿插。
