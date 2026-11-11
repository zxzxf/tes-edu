# T-HW 作业闭环 · 可执行任务卡（M3/M4/M5 共 22 项：代码已落地、迁移已完成，待真实数据与真机验收）

> 来源：`docx/学生Agent需求变更-任务方案书.md` v1.5（M0~M5 共 47 项）+ `docx/总任务清单与排期计划.md` v5.7（仪表盘：T-HW 47 项 / **25 完成（M0〜M2）/ 22 部分·待验收（M3〜M5）/ 0 未开始**）。
> 范围：本卡集覆盖**除 M0-2 外的 46 项**。每一项给出可直接开工的「步骤 + 验收 + 依赖 + 涉及文件/表 + 波次 + 估点」。
> 估点口径：拆分自方案书 §7（研发 128 人日 + 联调 26 人日 ≈154），**含前后端联调**，仅作排期参考。
>
> **2026-11-03 落地状态（历史记录）**：M3/M4/M5 共 22 项代码已全部落地并通过本地回归（`web-backend` 549、`web-frontend` 137、App 单测 144）。当时记录「137 上的 4 个迁移（0014~0017）未执行、未做真机 / 真实题库验收」，因此暂不把总清单仪表盘计为"已交付"。
>
> **2026-11-11 订正（连 137 → 106 生产库只读核实）**：迁移 `0014`~`0020` 共 7 个**全部已执行**（特征列 / 表齐全 + 14/14 迁移专属索引在位 + 回填数据佐证 `wrong_questions` 40 行 / `question_type` 非空 38 / `options` 非空 26 / 备份表 29 行），**无需补跑**。22 项仍不计「已交付」的唯一原因是**真实题库 / 真机数据验收未做**：`question_kp`、`course_source`、`crawl_job`、`crawl_run`、`kp_node`、`exam_paper_intake` 六张业务表均 0 行，仅 `exam_solutions` 8 / `exam_papers` 6 / `exam_paper_questions` 32 行有少量数据。详见《总任务清单与排期计划》v5.4。**【v5.5 订正】`question_kp` 已回填 474 行，其余 5 张表仍 0 行。** **【v5.6 订正】M4-2 服务端已验收并发版，其余 5 张表仍 0 行。** **【v5.7 订正】M5 五张空表已全部灌入真实数据，详见下方 v5.7 补记。**
>
> **2026-11-11 补记（v5.5 真实题库数据验收）**：`question_kp` 已由 0 → **474 行**（官方 `bind_from_course_kb` 回填 `school_pilot_01`，dry-run 先行、幂等复跑 created=0），**M3-3 / M3-4 / M3-5 服务端数据侧验收通过**：覆盖度 0.5829、数学 123/123 全覆盖、88 道未绑定题全为物理题（符合「物理降优先级不做细标」既定决策）；6 卷中 5 卷按卷图谱正常出图，`paper=31` 为 demo 中文标签卷 0 节点（非缺陷）；推题接口剔除多限定条件候选生效。**22 项计数仍为 25 完成 / 22 部分·待验收 / 0 未开始**——App 端真机呈现及其余 19 项尚未验收，M5 相关 4 表与 `kp_node` 仍 0 行。详见《总任务清单与排期计划》v5.5。 **【v5.6 订正】M4-2 已验收上线，其余项由 19 减为 18。** **【v5.7 订正】M5 全 6 项已验收，其余项由 18 减为 12。**
>
> **2026-11-11 补记（v5.6 M4-2 修复与正式发版）**：M4-2 规则组卷器 14 场景生产库 dry-run **首次验收不通过**（基线 10 题误报「知识点题源不足」，根因：69 道选择题 100% 多标签、63 个 kp 仅 1 题，按 kp 分配配额与全局 hash 去重冲突）。`666aeb2` 改题目级贪心最大覆盖 + 5 个真实分布单测（本地 582 passed），`2254e3f` 难度配比改按档位缺口紧迫度优先 + 1 个回归单测（本地 583 passed）。14 场景重跑：A/J/M/N 全部出卷成功、A 覆盖 40 kp，C/D/F/G 题源不足仍显式报错，B（7/10）/ L（6/10）经核验为客观题源不足，E 难度 1 由「实际 0」改善为「实际 3」；全程 rollback 零落库。**已正式发版 137**（镜像 `d404e001`，回滚点 `bak-20261111`），healthz / Kafka / 登录 JWT / 网关鉴权链全绿，线上 worker 已从新镜像加载新算法。**22 项计数仍为 25 完成 / 22 部分·待验收 / 0 未开始**，B 端组卷页与真机呈现未验。详见《总任务清单与排期计划》v5.6。**【v5.7 订正】v5.6 的「M5 灌真实数据」建议已于 11-11 执行。**
>
> **2026-11-11 补记（v5.7 M5 课程采集链路真实数据验收）**：用仓库真实素材（人教A版教材题 4 道 / 435 点大纲 / 高考细标题库包）经事务回滚式 dry-run 后真实落库，**五张空表全部灌满**：`course_source` 3（file/url/bank_pack，黑名单源阻断）、`crawl_job` 3、`crawl_run` 5（3 ok / 2 stopped）、`exam_paper_intake` 2（P1 卷 + P2 拆题各 1）、`kp_node` 4（全 draft）；另 +4 道 `auto_crawl` draft 教材题、+1 张未发布采集卷。`published` 池仍 213 行、已发布卷仍 6 张、`kb_knowledge_points` 仍 435 行，**未污染线上可用数据**。M5-4 审核发布与 `rollback_run` 一键回退均在事务内验证后 rollback（不留痕）。**22 项计数仍为 25 完成 / 22 部分·待验收 / 0 未开始**：B 端页面、教师审核流前端、LLM 抽取环节、真机呈现未验。回滚用官方 `rollback_run(run_id=6/7)`，元数据按 `【M5验收20261111】` / `M5V-` 前缀清理。脚本与素材落 137 `/opt/deploy/m5_sources/`、`/opt/deploy/_m5_pipeline.py` / `_m5_verify.py`。详见《总任务清单与排期计划》v5.7。
>
> 波次（取自主计划 §4.1 启动策略）：**W10** M0 → **W11** M1 → **W12** M2 → **W13** M4 → **W14** M3 → **W15** M5。与方案书 §9.2 首期 MVP（高三·数学·纯选择题卷）对齐：W10~W13 中 `paper_type=pure_choice` / `qtype=choice` 路径优先。

---

## ⚠️ 已知已落地前置（无需重做，仅收口/集成）

| 项 | 状态 | 说明 |
|----|------|------|
| **M0-2** 题目审核与脏题隔离 | ✅ 已落 | `status=draft/published` + `source_tag='quarantine'`；未审核题不可被组卷器抽取。本卡集不含此项。 |
| **M2-1 / M2-2 / M2-8** 等价判定纯函数 + gold set | 提前落地 | 纯函数 `equiv.py`（选择题字母/集合等价、多选 partial）、gold set 回归门禁已 commit（`b7965f4` / `cd1e556`）。本卡集仍列 M2-1/2/8 作为**集成收口**项，执行前先核对现状，避免重复造轮子。 |
| M0-3 / M1-8 / M2-11 | 源方案书个别处曾标 ✅2026-11-02 | 主计划仪表盘仍计「未开始」。本卡按「待启动/收口」处理，**开工前先 `git log` + 读相关文件确认是否已落地**，已落地则只补测试/接入。 |

> 红线（贯穿所有卡）：判据唯一来源 = 本卷 `exam_paper_question.answer_values`（ADR-A1）；判定权不在模型（ADR-A2）；自动判分词学生作答必须**结构化采集**（ADR-A9）；下放/组卷接口**永不返回** `standard_answer`/`answer_values`（ADR-A3）。

---

# W10 · M0 冷启动：统一题库 + 年级归属

## 卡 M0-1｜教师手动录题 + 批量导入（写入统一题库）
- **归口**：web-backend（录入/导入）+ web-frontend（教师端录题页）+ agent-backend（`ingest` 复用）
- **依赖**：无（首铲）
- **目标**：教师能把带 `qtype`/选项/答案/知识点的题写进统一题库 `course_kb`，并**双写** `course_kb_embeddings`（1024 维，ws 端点 `qwen3.7-text-embedding`）。
- **可执行步骤**：
  1. 教师端录题页：表单含 `stem / qtype(choice|blank|subjective) / choice_type / options / answer / answer_values(jsonb) / kp_codes / source=teacher`。
  2. 后端 `POST /api/admin/questions` → 写 `course_kb`（`status` 默认 `draft`）+ 调用 embedding 写 `course_kb_embeddings`（按 `content_hash` 关联）。
  3. 批量导入复用现有 `ingest.py`：支持 csv/json/xlsx，列名自动映射，知识点按 `；、/|,，` 拆分。
  4. **双写约束**：embedding 生成失败时题目仍入库但强制 `status=draft`，由后台任务补向量后转 `published`（见 M0-4 回填脚本）。
- **验收（可测）**：
  - 录 1 题后 `course_kb` 与 `course_kb_embeddings` 均有该 `content_hash` 行；
  - `status=draft` 题不可被组卷器（M4-2）抽取；
  - 批量导入 10 题，成功落库且 embedding 维度=1024。
- **涉及**：`course_kb`、`course_kb_embeddings`、`ingest.py`、教师端录题页；迁移见 M0-4。
- **波次/估点**：W10 / ≈5d
- **进度（2026-11-03，起手完成）**：
  - 后端 `POST /api/admin/questions` + `/batch`（`app/api/questions.py`）→ `app/services/question_entry.py`；
    写入恒 `draft`、`source_tag=teacher`、`tenant_id` 只取登录态；`content_hash` 与 Excel 导入同一指纹（`parser.normalize_question`），跨渠道不重复。
  - 向量双写 `app/services/embed.py`：租户 BYOM `embed_*` 优先、密钥/端点复用 chat 段，否则回落全局 `EMBED_*`；
    写 `course_kb_embeddings` 前校验 1024 维。**任何失败只降级不阻塞**：未配置/401/403/404/429/超时/维度不符 → 题目仍入库为 `draft`，响应带 `embedding.reason`。
  - 教师端 `web-frontend/src/views/Questions.vue`（菜单 `/ops/questions`，教师+学校角色授权），含单题表单、批量 JSON 粘贴、最近录入与向量状态提示。
  - 迁移 `migrations/0012_course_kb_entry_fields.sql`（`answer_values` / `choice_type` / `blank_count` + `(tenant_id,status)` 索引），**已在 137 线上库执行**（211 行不受影响）。
  - 未做：`kp_codes`（DAG code 归口 M3-3 / `resolve_kp_codes`，本轮只落 `knowledge_points` 标签）、生产环境实测（待发版）。

## 卡 M0-3｜学生年级归属（按年级段下发的前置）
- **归口**：web-backend（`student_profile` 表 + 接口）+ web-frontend（维护/批量导入页）+ student-app（读取自身 grade）
- **依赖**：无
- **目标**：建轻量 `student_profile(student_id, tenant_id, grade)`，学生沿用现有裸 `student_id`，**不建班级、不并入 `sys_user`**（ADR-A11）。
- **可执行步骤**：
  1. 迁移 `0005` 内新增 `student_profile` 表（`grade` 值域对齐 `course_kb.grade`：高一/高二/高三）。
  2. 教师端维护页 + `POST /api/admin/student-profile/batch` 批量导入（按 `student_id` upsert）。
  3. 兜底：未配置 `grade` 的 `student_id` 按**租户默认年级段**处理，避免登录后看不到任何卷。
  4. student-app 登录后拉取自身 `grade` 并缓存，供 M1-5 过滤。
- **验收（可测）**：
  - 导入 3 个学生（分属不同年级），`GET /api/student/profile` 返回正确 `grade`；
  - 未配置 grade 的学生返回租户默认值，能拉到该年级段卷。
- **涉及**：`student_profile`、`sys_user`（不改动）、student-app 登录链路。
- **波次/估点**：W10 / ≈3d

## 卡 M0-4｜`course_kb` 补 `tenant_id` 等字段
- **归口**：web-backend（迁移 + 旧数据回填）+ agent-backend（读取过滤）
- **依赖**：M0-1（录题写新字段）
- **目标**：`course_kb` 增加 `tenant_id / source / status / answer_values / choice_type / blank_count / reviewed_by / reviewed_at`；旧 211 行**统一归 `school_pilot_01`**（§6-13 定案）。
- **可执行步骤**：
  1. 写 `migrations/0005_*.sql`：ALTER `course_kb` 加列；`exam_*` 全套表一并建（见 M1-1）。
  2. 回填脚本：旧 211 行 `tenant_id='school_pilot_01'`、`status='published'`、`source='gaokao_real'`、`answer_values` 由 `answer` 解析（`answer_values` 生成逻辑复用 M2-4）。
  3. 题目读写接口强制 `tenant_id` 过滤（列出所有 `/api/admin/questions*`、RAG 检索侧接口，防跨租户读取）。
  4. 容器内 `docker exec -e PYTHONPATH=/app edu-web-backend python /tmp/x.py` 跑回填并校验。
- **验收（可测）**：
  - `course_kb` 全表 `tenant_id` 非空；
  - 跨租户查询旧题返回空（隔离生效）；
  - `create_all` 不会再加列（加列必须手工 ALTER，记忆红线）。
- **涉及**：`course_kb`、`migrations/0005`、`resolve_kp_codes`（kp 写侧唯一入口，禁改）。
- **波次/估点**：W10 / ≈6d
- **进度（2026-11-03）**：第 1、3 步已完成；第 2 步仅 `tenant_id`/`source_tag` 由 DEFAULT 归试点校，`answer_values` 回填尚无仓库脚本；第 4 步容器内校验本轮未执行。
  - 第 3 步收口两个读取面：（a）`GET /api/admin/questions` 与 `/search` 按登录态 `tenant_id` 过滤，
    网关漏注入时按 `school_pilot_01` 兜底而不是不过滤；（b）RAG 检索表 `course_kb_embeddings` 补 `tenant_id`
    （`0013_course_kb_embeddings_tenant.sql`），`rag._search_pg` / `_search_memory` 均带租户谓词，`orchestrator` 透传租户。
  - 写侧同步：教师录题双写、`import_bank`、`sync_embeddings` 均写入真实租户，不再靠默认值兜底。
  - 用例：`web-backend/tests/test_question_tenant_isolation.py`、`test_question_entry.py::test_embedding_rows_are_tenant_scoped`、
    `agent-backend/tests/test_rag_read.py`（含 dev 内存库同样隔离）。

---

# W11 · M1 作业闭环骨架（R1+R2 主链路）

## 卡 M1-1｜试卷域建表与迁移
- **归口**：web-backend
- **依赖**：M0-4（表结构基础）
- **目标**：落地 `exam_paper / exam_paper_question / exam_assign / exam_submission / exam_submission_item`（§2.1）。
- **可执行步骤**：
  1. `migrations/0005_exam_domain.sql`：五张表，含 `exam_paper_question.question_id NOT NULL`、`answer_values jsonb`、`blank_results jsonb`、`judge_source` 枚举。
  2. 复用现有 `wrong_questions`（不新建错题表），随迁移加可空 `paper_id`。
  3. 迁移可重复执行（`IF NOT EXISTS` + 幂等）；容器内校验表结构。
- **验收（可测）**：迁移在 137 容器跑通；`exam_paper_question.question_id` 非空约束在位；旧占位 `submissions` 表**不动**（新表另起）。
- **涉及**：`exam_*` 五表、`wrong_questions`（加列）。
- **波次/估点**：W11 / ≈2d

## 卡 M1-2｜试卷 CRUD + 发布
- **归口**：web-backend（`exam.py` 去 `_BANK`）+ web-frontend（教师端建卷页）
- **依赖**：M1-1
- **目标**：教师能建卷/加题/填答案/发布；**未发布卷学生不可见**。
- **可执行步骤**：
  1. `exam.py` 改造：用真实 DB 替代 `_BANK` 内存占位；`POST/GET/PUT /api/admin/papers`、`/papers/{id}/questions`。
  2. 发布接口 `POST /papers/{id}/publish`：触发 M1-3 题型校验、M4-6 答案必填校验、M2-9 G1 校验；发布后 `status=published`。
  3. 学生侧列表（M1-5）只查 `status=published`。
- **验收（可测）**：建卷→加 3 题→填答案→发布成功；`GET /api/student/exams` 在未发布前查不到该卷。
- **涉及**：`exam.py`、`exam_paper`、`exam_paper_question`。
- **波次/估点**：W11 / ≈3d

## 卡 M1-3｜题型枚举与发布校验
- **归口**：web-backend（校验逻辑）+ web-frontend（UI 标注）
- **依赖**：M1-2
- **目标**：`paper_type` 三值（pure_choice/pure_blank/mixed）枚举 + 卷内题型白名单校验；含解答题时 UI 标注「需人工评定 N 道」。
- **可执行步骤**：
  1. 校验器 `validate_paper()`：pure_choice 卷含 blank/subjective → 拦截；pure_blank 卷含 choice → 拦截；mixed 仅允许 choice+blank。
  2. 含 `subjective` 时统计数量，发布校验不拦（解答题允许入卷，只人工），但返回 violations 带 seq 级信息。
  3. `GET /papers/{id}/validation` dry-run 接口（发布前预览）。
  4. 前端发布页显式标「本卷含 N 道解答题，需人工评定」。
- **验收（可测）**：纯选择卷夹一道填空 → 发布返回 409 带 seq 级 violations；含 2 道解答题 → 通过但 UI 提示「需人工评定 2 道」。
- **涉及**：`validate_paper()`、`paper_type` 枚举。
- **波次/估点**：W11 / ≈2d

## 卡 M1-4｜题型→判分路由
- **归口**：agent-backend（`judge` 路由）/ web-backend（编排调用）
- **依赖**：M2-1/2/3（判定函数）
- **目标**：`choice`/`blank` 走确定性判定（0 token）；`subjective` 直落 `manual_pending` **且不调用 LLM**（ADR-A8）。
- **可执行步骤**：
  1. `judge(question, student_answer, pq)` 按 `pq.qtype` 分发：choice→`judge_by_paper_answer`（多选集合等价、缺选→partial）；blank→逐空等价；subjective→`manual_pending`+告警。
  2. **兜底**：题型缺失/答案缺失 → `manual_pending` + 告警（不猜）。
  3. map 查表仅作旁路交叉验证，**不作判据**；llm 只在错因归因/解析时调用（M2-11），绝不参与判定对错。
- **验收（可测）**：subjective 题提交后 `exam_submission_item.judge_source=manual` 且**无 LLM 调用日志**；choice 判断题 0 token（计数核对）。
- **涉及**：`judge_v2`/`judge`、`PROMPT_VERSION`（改提示词必须 bump）。
- **波次/估点**：W11 / ≈2d

## 卡 M1-5｜下发与作业列表（按年级段）
- **归口**：web-backend（`exam_assign`）+ student-app（作业列表）
- **依赖**：M0-3（年级归属）、M1-2
- **目标**：学生按自身 `grade` 拉取下发给该年级段的卷；租户 + 年级段双重过滤；同时支持按个人下发。
- **可执行步骤**：
  1. `POST /api/admin/assign`：`target_type=grade|student`，grade 段必填；写 `exam_assign`。
  2. `GET /api/student/exams`：以 `student_id→profile.grade` + `tenant_id` 过滤 `exam_assign`（**两层过滤**，防越权看别年级卷）。
  3. 定时下发/截止后自动提交（§6-2 待确认；首期可后置，先做手动下发）。
- **验收（可测）**：高三学生只看到下发给「高三」的卷；跨年级段查询返回空；同卷按个人下发的仅目标生可见。
- **涉及**：`exam_assign`、`student_profile`、网关 `jwt_c`（学生端 token）。
- **波次/估点**：W11 / ≈3d

## 卡 M1-6｜学生端答题页与提交
- **归口**：student-app（作业列表 + 答题页 + 提交）
- **依赖**：M1-5、M2-1/2/3（结构化输入契约）
- **目标**：断网草稿本地保存；**提交幂等**（client_id 去重）；自动判分词必须结构化输入（ADR-A9）。
- **可执行步骤**：
  1. 作业列表页（新）：从 `GET /api/student/exams` 渲染，点进答题页。
  2. 答题页按 `qtype` 渲染输入控件：choice=点选（收字母）、multi=多选（收字母集合）、blank=**分空输入框 + 符号键盘**（`/` `sqrt()` `^` `±` `π`）、subjective=文本+图片上传。
  3. **结构化提交**：服务端按 `blank_index` 收数组，**不做自由文本解析/OCR**。
  4. 断网草稿用 Room（参考 `ExamDraftStore`）本地暂存；`client_id` 去重保证提交幂等。
- **验收（可测）**：飞行模式下作答可暂存；恢复后提交成功；同一 `client_id` 重复提交只落一条 `exam_submission`。
- **涉及**：student-app 答题页、`ExamDraftStore`、提交接口 `POST /api/student/exams/{id}/submit`。
- **波次/估点**：W11 / ≈3d

## 卡 M1-7｜提交后编排判分
- **归口**：web-backend（编排）+ agent-backend（判定）
- **依赖**：M1-6、M1-4、M2-5
- **目标**：提交即返回；判分异步；单题失败不影响整卷。
- **可执行步骤**：
  1. 提交接口写 `exam_submission(status=grading)` 立即返回 `submission_id`。
  2. 异步编排：逐题调 `judge`（M1-4），写 `exam_submission_item`；单题异常 → 该 item `manual_pending`，整卷继续。
  3. 全卷判完 → `exam_submission.status=graded/partial_manual`，回填 `auto_score`。
  4. 检索失败题 → M2-5 工单；人工改判回写（M2-6）。
- **验收（可测）**：提交 100ms 内返回；故意让某题判分抛错，其余题正常出结论，该题为 `manual_pending`。
- **涉及**：`exam_submission`、`exam_submission_item`、异步队列（Kafka/后台 worker）。
- **波次/估点**：W11 / ≈2d

## 卡 M1-8｜学生端收敛「自由拍照解题」入口
- **归口**：student-app（路由改造）+ web-frontend（灰度开关）
- **依赖**：M1-6（作业入口就绪）
- **目标**：拍照题无法匹配已下发题目 → 提示「本题不在你的作业中」，不进判分；**讲解/图谱/错题集三模块保留**（R8）。
- **可执行步骤**：
  1. App 路由：自由拍照解题入口改为「先匹配已下发作业题」，匹配失败提示拒判。
  2. 灰度开关（复用 T-F 功能开关）控制入口可见性，可回退。
  3. 回归：确保 `SolveScreen`/`GraphScreen`/`WrongBook` 三模块未被误伤，旧错题本数据兼容。
- **验收（可测）**：对未下发题拍照 → 提示「不在作业中」且**无判分调用**；已下发题拍照可匹配进答题页；三模块均可正常打开。
- **涉及**：student-app 路由、`feature_switch`。
- **波次/估点**：W11 / ≈2d（注：源方案书曾标 ✅2026-11-02，开工前先核对 git 现状）

## 卡 M1-9｜结果回看
- **归口**：student-app（批改结果页）
- **依赖**：M1-7、M3-1（解析）
- **目标**：逐题显示对错 + 点评 + 步骤归因。
- **可执行步骤**：
  1. 结果页拉 `exam_submission_item`：`correct`/`judge_source`/`error_type`/`comment`/`first_wrong_step`。
  2. 渲染对错标 + 点评卡 + `StepsCard`（复用现有）。
  3. 提交前不可见解析（M3-2）；判错的题一键进错题集（M2-12）。
- **验收（可测）**：提交后结果页逐题显示 ✅/❌ + 错因；点击「看解析」在提交后可见（提交前隐藏）。
- **涉及**：student-app 结果页、`StepsCard`。
- **波次/估点**：W11 / ≈2d

---

# W12 · M2 自动阅卷确定性化（R2 质量）

## 卡 M2-1｜`judge_by_paper_answer()` 等价判定
- **归口**：agent-backend（`equiv.py` 纯函数）
- **依赖**：无（地基，可最先开工）
- **目标**：分数/小数/根式/±多解/区间/集合全展开；**0 token**。
- **可执行步骤**：
  1. 实现 `equiv(a, b)`：归一化（去空格、大小写、`61/25`≡`2.44`、`±2`≡`2`或`-2`、`sqrt(3)` 符号化比较）。
  2. 集合/多解：`answer_values` 任一匹配即判对。
  3. **纯函数、无 IO、可单测**；gold set 接入 CI（M2-8）。
- **验收（可测）**：`equiv("61/25","2.44")==True`；`equiv("±2","2")==True`；gold set 全绿。
- **涉及**：`equiv.py`、`judge_by_paper_answer`。**注：纯函数已提前落地（commit b7965f4），本卡为集成/收口 + 覆盖率补齐。**
- **波次/估点**：W12 / ≈2d

## 卡 M2-2｜选择题判定
- **归口**：agent-backend
- **依赖**：M2-1
- **目标**：单选字母等价 + 多选集合等价；缺选/多选 → `partial`。
- **可执行步骤**：
  1. 单选：`C`/`c`/`C. 12` 归一化为字母；按"值→选项"回查（学生写值而非字母时）。
  2. 多选：`{A,C}` 集合**完全相等**才对；缺/多 → `partial`（是否给分由 `partial_score_policy` 决定）。
  3. 连写展开（`ACD`）支持（记忆红线：曾只取首字母致多选全错）。
- **验收（可测）**：多选学生答 `ACD` 全对 → correct；答 `AC` → partial；单测覆盖字母/值/连写三种。
- **涉及**：`judge_by_paper_answer` 选择题分支。**注：已提前落地（cd1e556），本卡为收口。**
- **波次/估点**：W12 / ≈2d

## 卡 M2-3｜填空题判定
- **归口**：agent-backend
- **依赖**：M2-1
- **目标**：逐空等价（`blank_index`）+ 多解展开；多空全对得分 / 可按空配置。
- **可执行步骤**：
  1. 按 `blank_index` 逐空取 `student_answer[blank]` 与 `answer_values[blank]` 做 `equiv`。
  2. 默认**全对才得分**；`blank_results jsonb` 记录每空对错；`partial` 由 `partial_score_policy` 决定按空给分。
  3. 单位/大小写归一（`2m`≡`2 M`）；±多解。
- **验收（可测）**：双空题第一空对第二空错 → `partial` 且 `blank_results` 标第 2 空错；单测覆盖多空、单位、多解。
- **涉及**：`judge_by_paper_answer` 填空分支、`blank_results`。
- **波次/估点**：W12 / ≈2d

## 卡 M2-4｜答案值预展开入库
- **归口**：web-backend（录入/导入时）
- **依赖**：M0-1、M2-1
- **目标**：`answer_values` 落库（教师确认），主解漏解不再导致误判（等比 ±2 类问题）。
- **可执行步骤**：
  1. 录题/导入时，由 `answer` 推导 `answer_values`（多解展开），存 `course_kb.answer_values` 与 `exam_paper_question.answer_values`。
  2. 教师可在录题页**确认/编辑**展开结果（不自动覆盖，须经确认）。
  3. 复用 `equiv.py` 的展开逻辑，保证库内与判分用同一套。
- **验收（可测）**：等比题 `answer="±2"` → `answer_values=["2","-2"]`；判分用 `answer_values` 不再因主解只写 `2` 而误判 `-2`。
- **涉及**：`course_kb.answer_values`、`exam_paper_question.answer_values`。
- **波次/估点**：W12 / ≈2d

## 卡 M2-5｜检索失败 → 人工评定工单
- **归口**：web-backend（状态机）+ agent-backend（失败检测）
- **依赖**：M1-7
- **目标**：检索不到本卷答案的题 100% 进 `manual_pending` 队列，**不猜**。
- **可执行步骤**：
  1. 判分编排中：拿不到 `pq.answer_values` 或题型/答案缺失 → item 置 `manual_pending` + 发 Kafka 事件。
  2. `exam_submission_item.status=manual_pending` 即工单载体（不另起工单表，避免双写）。
  3. 教师端按 `status=manual_pending` 过滤即待办列表（M2-6）。
- **验收（可测）**：构造一题无答案的卷，提交后该 item 必为 `manual_pending` 且**无 LLM 判定日志**。
- **涉及**：`exam_submission_item.status`、`manual_pending` 状态机。
- **波次/估点**：W12 / ≈2d

## 卡 M2-6｜教师端人工评定页
- **归口**：web-frontend（`Grading.vue` 改造）
- **依赖**：M2-5
- **目标**：待办列表、逐题判定、改判留痕、结果回写学生端。
- **可执行步骤**：
  1. `Grading.vue`：列表过滤 `manual_pending`；逐题展示学生作答 + 本卷答案（教师可见）。
  2. 判定操作：判对/错/partial，写 `manual_result` + `manual_by` + 留痕；回写 `exam_submission_item` + 重算 `exam_submission.final_score`。
  3. 改判触发 G3 复查（M2-10）；判为正确时同步软删 `wrong_questions`（M2-12）。
- **验收（可测）**：教师改判 1 题 → 学生端结果页更新；`final_score` 重算正确；审计留痕可见。
- **涉及**：`Grading.vue`、`exam_submission_item`。
- **波次/估点**：W12 / ≈3d

## 卡 M2-7｜判分指标看板
- **归口**：web-backend（指标聚合）+ web-frontend（看板）
- **依赖**：M1-7、M2-6
- **目标**：判据覆盖率 ≥99%；人工复核率可观测（§8.3 口径）。
- **可执行步骤**：
  1. 落库 `exam_submission_items.grading_source`（判据来源，事后现算会被教师改动污染，必须落库——记忆红线）。
  2. 聚合：判据覆盖率分母**只含 choice+blank**；人工复核率用「曾需人判(pending+reviewed)/总数」而非瞬时 pending；判分准确率 `measurable=false` 不伪造；0/0→null。
  3. 按卷/教师/知识点下钻；新卷首周强制 100% 抽样。
- **验收（可测）**：看板显示「判据覆盖率 X% / 人工复核率 Y%」；分母不含 subjective；SQLite 读 `DateTime` 用 `_aware()` 处理 naive/aware 差异（记忆坑）。
- **涉及**：`exam_submission_items.grading_source`、指标 API。
- **波次/估点**：W12 / ≈2d

## 卡 M2-8｜Gold set 回归门禁
- **归口**：web-backend（评测集）+ CI
- **依赖**：M2-1（纯函数）
- **目标**：30~50 题评测集接入 CI，判分准确率不达标不允许合并。
- **可执行步骤**：
  1. 收编验证题（歧义 `a=1`、等比 `±2`、切线 `a=4`、期望 `61/25`、平均数选 C 等）为 gold set，标注期望判定。
  2. CI job 跑 gold set，准确率 < 阈值（首月 ≥95% / 稳定 ≥98%）则 fail。
  3. **每暴露一个等价写法没覆盖到的，补一个单测永久修掉**（§8.5）。
- **验收（可测）**：`build-images.yml`/CI 中 gold set job 绿；新增等价写法失败能阻断合并。**注：已提前落地（cd1e556），本卡为集补充与阈值固化。**
- **波次/估点**：W12 / ≈1d

## 卡 M2-9｜G1 答案双源交叉校验
- **归口**：web-backend（校验器，位于 M4-6 发布前）
- **依赖**：M0-1、M4-6
- **目标**：有第二来源时 2 取 2 一致才允许发布；不一致标红转教师裁决；覆盖率计入「答案可信率」。
- **可执行步骤**：
  1. `exam_answer_checks` 表（一题一行）：答案改了旧确认自动失效（用 answer 列做锚点）。
  2. 四档：`verified`（题库现行答案 ≠ 卷面快照才是独立第二来源）/ `mechanical_pass`（单变量等式+数值答案→代入检验，代入失败只标 suspect 不报硬冲突）/ `teacher_confirmed` / 其余 `unverified`·`conflict` → 409。
  3. 已发布卷**不许改答案**（历史成绩判据会变，属改分事故）。
  4. LLM 只在 `ANSWER_VERIFY_LLM=1` 时给建议（`llm_*` 字段），**永不作为通过依据**（§8.2 红线）。
- **验收（可测）**：双源冲突的卷发布 → 409；`mechanical_pass` 代入失败只标 suspect 不硬拦。
- **涉及**：`exam_answer_checks`、发布闸门顺序（结构→QUESTION_FROZEN→ANSWER_UNVERIFIED/CONFLICT）。
- **波次/估点**：W12 / ≈2d

## 卡 M2-10｜G3 改判触发复查
- **归口**：web-backend（冻结逻辑）+ 告警
- **依赖**：M2-6（改判）
- **目标**：同一答案累计改判 K 次（默认 3，`GRADING_FREEZE_THRESHOLD`）自动冻结该题并告警。
- **可执行步骤**：
  1. 判定护栏键 `(tenant_id, content_hash, 标答指纹)`（**不是 paper_question_id**）；指纹只做书写层归一，不做语义展开。
  2. 两条红线：**首次判定不计**（否则任何填空三人就冻结）、**判定没变不计**。
  3. 冻结三效果：判分首行短路（任何卷、选择题也拦）/ 发布闸门 409 `QUESTION_FROZEN` / 告警流水 + 影响面。
  4. 接口 `/grading/freezes`、`{id}/ack`、`{id}/release`（理由必填、计数清零）、`/grading/alerts`。
- **验收（可测）**：某答案被改判满 K 次 → 该题进入冻结，新提交该题全转人工；`release` 后恢复。
- **涉及**：`GRADING_FREEZE_THRESHOLD`、冻结接口。
- **波次/估点**：W12 / ≈2d

## 卡 M2-11｜解析/归因提示词规范
- **归口**：agent-backend（提示词 + `PROMPT_VERSION`）
- **依赖**：M1-4（判分主链路已不调 LLM）
- **目标**：只约束「解析生成」与「错因归因」；模型不得输出与卷面答案冲突的数值（复用数值护栏）。
- **可执行步骤**：
  1. 提示词明确：LLM 仅做错因归因/解析，**不参与判定对错**；禁止复述「检索无结果」等内部状态。
  2. 引导档严禁给数值结论（唯一例外「一眼能报出数」）；系统提示禁止复述内部状态。
  3. 改提示词必须 bump `PROMPT_VERSION`；**降级结果绝不进缓存**（缓存键含 model@PROMPT_VERSION）。
- **验收（可测）**：解析生成输出数值与卷面答案一致；`PROMPT_VERSION` 变更后缓存键随之变（不返回旧答案）。
- **涉及**：`llm._remote_judge`、提示词、`PROMPT_VERSION`。**注：源方案书曾标 ✅2026-11-02，开工前核对 git 现状。**
- **波次/估点**：W12 / ≈1d

## 卡 M2-12｜判错题自动进错题集（R8）
- **归口**：web-backend（`student_event_worker`）+ student-app（`WrongBook`）
- **依赖**：M1-7、M2-6
- **目标**：判错即落 `wrong_questions`，标 `source='exam'` 并带 `content_hash`/`error_type`/`first_wrong_step`；**幂等不重复**；人工改判为正确时同步软删。
- **可执行步骤**：
  1. 复用现有 `student_event_worker`（判错自动写 `wrong_questions`），只加 `source='exam'` + `paper_id`。
  2. **幂等**：`UNIQUE(submission_id, submission_item_id)` 在提交时就地开单（同事务，不走 Kafka）；已存在则跳过。
  3. 人工改判正确 → 软删该题（带 `client_id` 云同步键）。
- **验收（可测）**：判错 1 题 → `wrong_questions` 新增 1 行 `source='exam'`；重复提交不重复落；改判正确 → 该行软删。
- **涉及**：`wrong_questions`、`student_event_worker`、`WrongSyncMerger`。
- **波次/估点**：W12 / ≈3d

---

# W13 · M4 教师出卷与自动组卷（R5）

## 卡 M4-1｜手动出卷
- **归口**：web-frontend（教师端组卷页）+ web-backend
- **依赖**：M1-2、M0-1
- **目标**：教师端支持从题库按 kp+难度筛选题目，选题/组卷/配答案/分值。
- **可执行步骤**：
  1. 组卷页：筛选器（kp / 难度 / 题型 / 学科 / 年级段）+ 题库列表 + 已选卷面。
  2. 每题配 `score`、`standard_answer`、`answer_values`（预展开 M2-4）。
  3. 存 `exam_paper` + `exam_paper_question`（引用 `question_id` + 快照）。
- **验收（可测）**：教师筛选高三·数学·选择题→选 5 题→配分→保存为草稿卷。
- **涉及**：组卷页、`exam_paper_question`。
- **波次/估点**：W13 / ≈3d
- **状态**：✅ 已完成：教师端 `Exam.vue` 改为「年级/学科/卷型/kp/难度 → 题库列表 → 勾选 → 逐题配分 → 保存草稿」，后端 `POST /exam/papers/manual` 落 `exam_paper_question`（引用 `question_id` + 快照，标准答案随卷保存）。

## 卡 M4-2｜规则组卷器（核心）
- **归口**：web-backend（组卷引擎）
- **依赖**：M0-4、M2-4
- **目标**：输入 `grade`(必填)+`subject`+`paper_type`+题量+难度分布+kp 覆盖要求，自动成卷。
- **可执行步骤**：
  1. 只抽 `status=published`、非 `quarantine`、**有答案**的题；同卷 `content_hash` 不重复。
  2. 题型须与卷类型白名单一致（M1-3）。
  3. **权重（v1.4 定案）**：知识点覆盖优先 → 其次题量/难度分布；同类题优先教师题（`teacher > import > crawl > llm_generated`）。**【2026-11-11 v5.6 订正】传 `difficulty_mix` 时改为按「档位缺口紧迫度」先补稀缺难度档；未传时仍严格按 v1.4 覆盖优先；同类题优先级不变。**
  4. **题源不足时显式报错并指明缺哪个维度**（实测填空仅 21 道、难度 1 仅 3 道，不可静默凑卷）。
  5. 难度比例做成可配置，抽不满时提示「易题不足，实际分布 X」。
- **验收（可测）**：输入高三·数学·pure_choice·10题 → 返回合规卷；强制要 5 道填空 → 显式报错「填空题源不足（现有 21）」。
- **涉及**：组卷引擎、`exam_paper_question`、题库过滤。
- **波次/估点**：W13 / ≈6d
- **状态**：✅ 已完成：`core/compose_engine.py` + `services/paper_compose.py` 只抽 `published`/非 `quarantine`/有答案/本租户/题型白名单/难度区间的题，同卷 `content_hash` 去重，kp 覆盖优先、同题优先 `teacher > import > crawl > llm_generated`；题源不足返回 409 并列出缺口维度，不静默凑卷。 **【2026-11-11 生产实测 + 修复上线】首次 14 场景 dry-run 验收不通过**（基线 10 题误报题源不足，根因见上「权重」订正）；`666aeb2` 题目级贪心最大覆盖 + 5 单测（582 passed），`2254e3f` 难度档位紧迫度 + 1 单测（583 passed）；重跑 14 场景 A/J/M/N 全部出卷、A 覆盖 40 kp，C/D/F/G 仍显式报错，B/L 为客观题源不足，E 难度 1 由 0 → 3。**已正式发版 137（镜像 `d404e001`，回滚点 `bak-20261111`），线上 worker 已加载新算法**；B 端组卷页与真机呈现未验，本卡仍记 🟡。

## 卡 M4-3｜LLM 辅助组卷
- **归口**：web-backend（LLM 生成）+ web-frontend
- **依赖**：M4-2（最终仍走规则校验）
- **目标**：按主题生成草稿卷；生成的题与答案一律 `draft`，**必须教师确认才发布**。
- **可执行步骤**：
  1. `POST /api/admin/papers/llm-generate` 按主题调 LLM 出草稿（题+答案）。
  2. 草稿进 `course_kb(status=draft, source=llm_generated)` + 组卷候选。
  3. **不自动发布**：教师确认后转 M4-2 规则校验再发布。
- **验收（可测）**：生成 10 题草稿 → 库内 `status=draft`；未确认前 `GET /api/student/exams` 不可见。
- **涉及**：LLM 组卷、`course_kb.source`。
- **波次/估点**：W13 / ≈3d
- **状态**：✅ 已完成：`services/llm_paper.py` + `POST /admin/exam/papers/llm-generate` 只写 `course_kb(status=draft, source_tag=llm_generated)`；不自动发布，未审核前学生端不可见。

## 卡 M4-4｜采集卷入库两条路径（都做）
- **归口**：web-backend（采集入库）+ web-frontend（教师选路径）
- **依赖**：M4-2、M3-8（采集任务）
- **目标**：P1 整卷入库 / P2 拆题重组，采集时由教师选择；两条均落 `draft` 待审、带来源追溯、可一键回退。
- **可执行步骤**：
  1. P1：成套卷直接落 `exam_paper(source=auto_crawl)`，其题目仍**逐条进 `course_kb`**（保证题库统一可复用）。
  2. P2：采集题先进库，再由 M4-2 按规则重组新卷。
  3. 采集任务带 `path` 选项供教师选；来源追溯 + 一键回退。
- **验收（可测）**：选 P1 采集一套卷 → `exam_paper.source=auto_crawl` 且题目入 `course_kb`；选 P2 → 题目入 `course_kb` 但未直接成卷。
- **涉及**：采集入库、`exam_paper.source`、`crawl_job`。
- **波次/估点**：W13 / ≈4d
- **状态**：✅ 已完成：`services/crawl_intake.py` 支持 P1 建 `exam_paper(source=auto_crawl)` 且题目逐条入 `course_kb`、P2 只入题库；两条都 `draft`，来源可追溯，`POST /course/runs/{id}/rollback` 只撤本次 `draft/auto_crawl`，不动教师改过的题。

## 卡 M4-5｜试卷版本与复用
- **归口**：web-backend
- **依赖**：M1-2
- **目标**：`version` + 复制/另存；改卷不影响已下发卷的判据。
- **可执行步骤**：
  1. `exam_paper.version` 自增；复制/另存为新卷（新 `id`，引用同 `question_id`）。
  2. 已下发卷的 `exam_paper_question` 是快照，改源卷不影响判定（ADR-A6 缓存与版本）。
- **验收（可测）**：复制卷独立编辑，原卷已下发题判定不受影响。
- **涉及**：`exam_paper.version`、复制接口。
- **波次/估点**：W13 / ≈2d
- **状态**：✅ 已完成：`exam_paper.version`（迁移 0014）+ `POST /exam/papers/{id}/copy`；副本新 id、新快照、草稿态，原卷已下发判据不受影响。

## 卡 M4-6｜答案必填 + G1 双源校验
- **归口**：web-backend（发布前校验）
- **依赖**：M2-4、M2-9
- **目标**：无答案的题目不允许发布；有第二来源时 2 取 2 一致，否则转教师裁决。
- **可执行步骤**：
  1. 发布闸门：`exam_paper_question.standard_answer` 为空 → 拦截（409）。
  2. 调 M2-9 G1 校验器：双源不一致 → 转教师裁决；`conflict` → 409。
  3. 已发布卷改答案 → 拦（见 M2-9）。
- **验收（可测）**：含 1 道无答案题的卷发布 → 409；双源冲突 → 转人工裁决不发布。
- **涉及**：发布闸门、`exam_answer_checks`。
- **波次/估点**：W13 / ≈2d
- **状态**：✅ 已完成：`core/publish_guard.py` 发布闸门按「结构 → ANSWER_MISSING → SCORE_MISSING → QUESTION_UNREVIEWED → QUESTION_FROZEN → G1 双源 → G2 抽样」顺序拦截；无答案/双源冲突均 409 并给出明细。

## 卡 M4-7｜G2 新卷抽样复核
- **归口**：web-backend（抽样空跑）
- **依赖**：M2-1/2/3（判定函数）
- **目标**：新卷发布前用标准学生样本空跑，判定分布异常即拦截。
- **可执行步骤**：
  1. 构造样本：正确解 + 常见错法 + 等价写法 + 边界写法。
  2. 发布前对样本跑判定，分布异常（如正确解被判错、常见错法被判对）即拦截。
  3. 新卷首周强制 100% 抽样（§8.3）。
- **验收（可测）**：构造「正确解被判错」的卷 → 发布被 G2 拦；正常卷通过。
- **涉及**：G2 抽样管线、发布闸门。
- **波次/估点**：W13 / ≈5d
- **状态**：✅ 已完成：`publish_guard.sample_report` 对每题跑「正确解 / 常见错法 / 等价写法 / 边界写法」四类标准样本；正确解被判错或常见错法被判对即拦，并回传逐题样本明细。

---

# W14 · M3 解析与知识点（R3+R4）

## 卡 M3-1｜按卷题维度生成解析
- **归口**：agent-backend（解析生成）+ web-backend（落库）
- **依赖**：M1-7、M2-11
- **目标**：`POST /exams/{id}/questions/{qid}/solution`，有 `solution_steps` 直接返回，无则生成并落库（幂等）。
- **可执行步骤**：
  1. 接口按 `(paper_id, question_id)` 查 `solution_steps`；有则返回。
  2. 无则调 LLM 生成（受 M2-11 提示词约束），落 `exam_paper_question.solution_steps`（或独立表）。
  3. **幂等**：同 key 不重复生成（防刷）。
- **验收（可测）**：首次请求生成并落库；二次请求直接返回缓存；不重复生成。
- **涉及**：`/exams/{id}/questions/{qid}/solution`、`solution_steps`。
- **波次/估点**：W14 / ≈2d
- **状态**：✅ 已完成：`services/solution.py` + 学生 `GET /student/exams/{paper_id}/questions/{seq}/solution`、教师 `GET /admin/exam/papers/{id}/solutions?generate=true`；按 `(paper_id, paper_question_id)` 幂等，已有则回缓存、无则生成落库。并发防刷已补：ORM 声明 `uq_exam_solution` 唯一键，撞键时回读先到者结果而非 500（`test_solution_concurrent_insert_returns_cached_row`）。

## 卡 M3-2｜解析时机控制
- **归口**：web-backend（权限/状态校验）+ student-app（UI 控制）
- **依赖**：M3-1
- **目标**：学生提交前**不可**查看本题解析（防抄）；教师端始终可见。
- **可执行步骤**：
  1. 接口层：未提交/未批改的卷，`/solution` 返回 403（学生端）。
  2. 学生端：结果页前隐藏「看解析」入口；提交后显示。
  3. 教师端不受限。
- **验收（可测）**：未提交卷调 `/solution` → 403；提交后 → 200。
- **涉及**：`/solution` 权限、`ExamViewModel` UI 控制。
- **波次/估点**：W14 / ≈1d
- **状态**：✅ 已完成：未提交访问学生解析返回 403 `SOLUTION_LOCKED`；提交后才按需生成/命中缓存（教师端不受限）。

## 卡 M3-3｜题↔知识点绑定
- **归口**：web-backend（`question_kp`）+ web-frontend（标注工具）
- **依赖**：M0-1
- **目标**：`question_kp` 绑定 + 覆盖度可统计；未绑定题进待标注队列。
- **可执行步骤**：
  1. 录入/导入时写 `question_kp(question_id, kp_code, weight, source, confidence, reviewed_by)`。
  2. 批量标注工具（教师端）：按知识点批量绑题。
  3. 覆盖度统计：按 kp 聚合题量，未绑定题进待标注队列。
- **验收（可测）**：绑 1 题到 2 个 kp → `question_kp` 2 行；覆盖度看板显示该 kp 题量。
- **涉及**：`question_kp`、标注工具。**注意 kp_code 写侧唯一入口 `resolve_kp_codes`，禁改。**
- **波次/估点**：W14 / ≈2d
- **状态**：✅ 已完成：`question_kp` 表（迁移 0015）+ `services/question_kp.py`；`POST /exam/question-kp/bind`、`/backfill`、`GET /coverage`，未绑定题进待标注队列；`kp_code` 写侧统一走 `mastery.resolve_kp_codes`。 **【2026-11-11 生产实测】** 真实题库回填 474 行（数学 123/123 绑定），覆盖率 0.5829、`by_kp` 题量正确、未绑定 88 题（全物理）进待标注队列；幂等复跑 created=0。卡面数据侧验收通过，App 端真机仍待验。

## 卡 M3-4｜知识体系可视化
- **归口**：web-frontend（图谱页）+ student-app（GraphScreen 复用）
- **依赖**：M3-3
- **目标**：按卷展示本题涉及知识点及其前驱后继（复用现有 Graph）。
- **可执行步骤**：
  1. 教师端/学生端图谱页：输入卷 → 聚合该卷题的 `kp_codes` → 渲染 DAG 子图。
  2. 高亮前驱/后继（读 `kb_kp_prerequisites`）。
- **验收（可测）**：打开某卷图谱 → 显示该卷所有题的知识点及依赖关系。
- **涉及**：`GraphScreen`、`kb_knowledge_points`、`kb_kp_prerequisites`。
- **波次/估点**：W14 / ≈3d
- **状态**：✅ 已完成：`graph.paper_graph_db` 聚合卷内 kp 并高亮前驱后继，教师 `/admin/exam/papers/{id}/graph`、学生 `/student/exams/{paper_id}/graph`；App `GraphScreen` 支持按卷维度，`AppBridge.postPaperGraph` 承接跳转。 **【2026-11-11 生产实测】** 6 卷中 5 卷出图（nodes 36~65 / `in_paper` 16~31 / `unresolved=[]`）；`paper=31`（demo 中文标签卷）0 节点属数据口径，非代码缺陷。App 端真机仍待验。

## 卡 M3-5｜触类旁通推题
- **归口**：agent-backend（复用 `similar`）+ web-backend
- **依赖**：M3-3
- **目标**：推荐与本题同知识点且**条件一致**的题（复用 `extra_conditions`）。
- **可执行步骤**：
  1. 按本题 `kp_codes` 调 `similar` 召回候选。
  2. 过滤「题库题比学生题面多限定条件」的候选（标答可信性判定，记忆红线）。
  3. 返回同条件变式。
- **验收（可测）**：对某等比题推题 → 返回同知识点同条件变式，不含多限定条件的干扰题。
- **涉及**：`similar`、`extra_conditions`、`question_kp`。
- **波次/估点**：W14 / ≈2d
- **状态**：✅ 已完成：`services/similar_kp.py` 复用 agent 侧 `extra_conditions`，把「题库题比学生题面多限定条件」的候选一律剔除；学生端点 `/student/exams/{paper_id}/similar`。 **【2026-11-11 生产实测】** paper=32 seq=1 绑到 2 个 code、返回 3 候选且 `excluded=1`（多限定条件题被剔除），确认下游用上 `question_kp`；seq=2 同条件题不足返回 0 条属正常。

## 卡 M3-6｜错题 → 知识点 → 背诵卡联动
- **归口**：web-backend + student-app（背诵卡 SM-2）
- **依赖**：M2-12、M3-3
- **目标**：判错的题按其知识点自动生成/推荐记忆卡（回应需求「方便记忆」）。
- **可执行步骤**：
  1. 判错落 `wrong_questions(source=exam)` 时，取该题 `kp_codes`。
  2. 按 kp 触发背诵卡 SM-2 生成/推荐（复用现有背诵卡逻辑）。
  3. 重做后按掌握度调整背诵卡调度。
- **验收（可测）**：判错 1 题 → 其 kp 对应背诵卡被推荐；重做正确 → 掌握度上调、卡调度变化。
- **涉及**：背诵卡 SM-2、`wrong_questions`、`kp_codes`。
- **波次/估点**：W14 / ≈3d
- **状态**：✅ 已完成：`services/drill_link.py` 按错题 kp 生成/推荐背诵卡（同学生同 kp 只一张）；重做正确只顺延 `due_at`，不改 SM-2 参数；判错落卡与改判回收均有单测。

## 卡 M3-7｜新题录入（教师）
- **归口**：web-frontend + web-backend
- **依赖**：M0-1、M0-2
- **目标**：教师端单题/批量录入 + 审核；录入题默认 `draft`，审核后 `published`。
- **可执行步骤**：
  1. 复用 M0-1 录题页，增加审核流（reviewer 角色）。
  2. 审核后 `status=published` 方可被组卷抽取。
  3. 与 M0-2 隔离机制联动（`quarantine` 标红）。
- **验收（可测）**：教师录 1 题默认 `draft`；审核后 `published`；`draft` 不可组卷。
- **涉及**：录题页、审核流、`course_kb.status`。
- **波次/估点**：W14 / ≈2d
- **状态**：✅ 已完成：复用录题页 + `POST /exam/questions/{id}/review`；新录入恒 `draft`，审核后 `published` 才能被组卷抽取，`quarantine` 在审核面板标红隔离。

## 卡 M3-8｜新题定时采集
- **归口**：web-backend（`crawl_job` + worker）
- **依赖**：M0-1、M3-7
- **目标**：按 cron 执行、去重、入库 `draft`、超预算自动停。
- **可执行步骤**：
  1. `crawl_job` 表 + 采集 worker：按 `schedule(cron)` 跑，写 `course_kb(status=draft, source=crawl)`。
  2. 去重（按 `content_hash`）；`budget_limit` 超限自动停 + 告警。
  3. 入库后转 M3-7 审核流。
- **验收（可测）**：配置一个每分钟测试 job → 执行并入库 `draft`；设 `budget_limit=1` → 超预算即停。
- **涉及**：`crawl_job`、采集 worker、预算告警。
- **波次/估点**：W14 / ≈3d
- **状态**：✅ 已完成：`services/crawl.py` 提供 cron 解析、`next_run_at`、`due_jobs`、`run_due_jobs`、`scheduler_loop`；web-backend lifespan 启动循环（`CRAWL_SCHEDULER_ENABLED/INTERVAL`）；新建任务默认 `paused`，`POST /admin/course/jobs/{id}/status?status=active|paused|stopped` 启停（启用时重算 `next_run_at`，非法 cron 拒绝，`CourseIntake.vue` 有「启用定时 / 暂停定时」按钮）；`content_hash` 去重、`budget_limit` 超限立即停并置 `status=stopped`。

## 卡 M3-9｜学生端三模块保留与打通（R8）
- **归口**：student-app（讲解/图谱/错题集）
- **依赖**：M3-1、M3-4、M2-12
- **目标**：① 讲解：卷内题可查解析（提交前不可见）；② 图谱：按卷展示本题知识点及前驱后继；③ 错题集：可按卷筛选、支持重做，重做后按 M3-6 调整掌握度与背诵卡。
- **可执行步骤**：
  1. 讲解：`SolveScreen` 在作业场景接入 M3-1（提交前隐藏）。
  2. 图谱：`GraphScreen` 支持「按卷」维度（M3-4）。
  3. 错题集：`WrongBook` 增加「按卷筛选」+ 重做入口；重做经由 M3-6 回写掌握度/背诵卡。
- **验收（可测）**：作业场景三模块均可打开且数据正确；错题按卷筛选成功；重做后掌握度变化。
- **涉及**：`SolveScreen`、`GraphScreen`、`WrongBook`、`WrongSyncMerger`。
- **波次/估点**：W14 / ≈5d
- **状态**：✅ 已完成：App 结果页可查卷内解析（提交前隐藏）、`GraphScreen` 按卷展示、错题本按卷筛选；新增「重做并判分」弹窗调 `POST /learning/wrong-questions/{id}/redo`，判对后服务端归档并顺延背诵卡，App 同步移除本地行。`RedoFeedbackTest` 11 例覆盖转人工/不可用/部分分文案与多空拆分。

---

# W15 · M5 课程采集与知识体系拆分（R6）

## 卡 M5-1｜课程源管理
- **归口**：web-backend（`course_source`）+ web-frontend
- **依赖**：无（可与 M0 并行启动）
- **目标**：`course_source` 表 + 教师端页面；支持文件/URL/题库包；采集任务可启停。
- **可执行步骤**：
  1. 建 `course_source(id, tenant_id, name, source_type, uri, parser, status, ingested_at)`。
  2. 教师端页面：增删改查 + 启停采集任务。
- **验收（可测）**：建 1 个文件源 + 1 个 URL 源 → 列表可见；启停状态切换生效。
- **涉及**：`course_source`、教师端页面。
- **波次/估点**：W15 / ≈3d
- **状态**：✅ 已完成：`course_source`（迁移 0015）+ `GET/POST/DELETE /admin/course/sources`、`POST /sources/{id}/status`；教师页 `CourseIntake.vue` 支持增删、启停、文件/URL/题库包三类源。**【2026-11-11 生产实测】** 建 3 个真实源（file / url / bank_pack），合规黑名单「禁止转载」被前置阻断且未落库，源启停 `active↔paused` 生效、列表返回 3 行；`course_source` 0→3。

## 卡 M5-2｜课程解析 pipeline
- **归口**：agent-backend（解析）
- **依赖**：M5-1
- **目标**：章节切分 → 知识点抽取，输出结构化大纲。
- **可执行步骤**：
  1. 解析器按 `source_type` 选 parser（教材/大纲/视频/网页/题库包）。
  2. 章节切分 → 段落/知识点候选。
  3. 输出结构化大纲（章节树 + 候选知识点）。
- **验收（可测）**：喂一份教材 PDF/大纲 → 输出结构化章节树。
- **涉及**：`course_source.parser`、解析 pipeline。
- **波次/估点**：W15 / ≈4d
- **状态**：✅ 已完成：`services/course_pipeline.py` 按 `source_type` 选解析器，输出章节树 + 候选知识点；`POST /admin/course/parse` 不落库先看结构化大纲。**【2026-11-11 生产实测】** 喂真实 435 点人教A版大纲 md（标题层级归一后）→ **5 册 / 19 节 / 435 个候选知识点**；原始 md 直喂得 1 章 / 437 点；纯函数不落库。

## 卡 M5-3｜知识点拆分与挂载
- **归口**：agent-backend（LLM 抽取）+ web-backend（`kp_node`）
- **依赖**：M5-2
- **目标**：LLM 抽取 → `kp_node` → DAG 归并；新知识点默认 `draft`；与已有 DAG 去重合并。
- **可执行步骤**：
  1. LLM 从大纲抽取知识点 → 写 `kp_node(code, course_id, parent_code, name, source=crawled, status=draft)`。
  2. 与已有 `kb_knowledge_points` DAG 去重合并（code 体系一致，走 `resolve_kp_codes`）。
  3. 新节点默认 `draft`。
- **验收（可测）**：抽取的知识点去重后入库；重复知识点不重复建节点；新节点 `status=draft`。
- **涉及**：`kp_node`、`kb_knowledge_points`、`resolve_kp_codes`（唯一写侧）。
- **波次/估点**：W15 / ≈4d
- **状态**：✅ 已完成：`upsert_kp_nodes` 先走 `mastery.resolve_kp_codes`——已进 DAG 的归并（`merged`），新节点 `status=draft`，不再造第二套 code 体系。**【2026-11-11 生产实测】** 4 个真实教材考点（新 code）+ 2 个 DAG 已有 code → `created=4 / merged=2 / skipped=0`，幂等复跑 `created=0 / skipped=4 / merged=2`；4 个新节点全 `draft` 且在 `kb_knowledge_points` 命中 0；`kp_node` 0→4。（本轮用真实大纲的确定性候选点，**未跑 LLM 抽取**，该环节仍待验）

## 卡 M5-4｜知识点审核与发布
- **归口**：web-frontend（审核流）+ web-backend
- **依赖**：M5-3
- **目标**：教师端审核流；未审核知识点不进学生端图谱。
- **可执行步骤**：
  1. 审核页：列出 `kp_node(status=draft)` 供教师审。
  2. 审核通过 → `status=published`；接入 `kb_knowledge_points` 供图谱使用。
- **验收（可测）**：`draft` 知识点学生端图谱不可见；审核后可见。
- **涉及**：`kp_node.status`、审核流。
- **波次/估点**：W15 / ≈3d
- **状态**：✅ 已完成：`POST /admin/course/kp-nodes/{code}/review`；`draft` 不进学生图谱，审核通过写 `kb_knowledge_points` 后可见，可退回。**【2026-11-11 生产实测】** 事务内验证：发布后 DAG 命中 1、`kp_node.status=published`，退回 `draft` 后命中 0；验证后整体 rollback，`kb_knowledge_points` 仍 435 行不受污染。**B 端审核页 / 教师审核流前端未验。**

## 卡 M5-5｜采集合规与预算
- **归口**：web-backend（合规校验 + 预算）
- **依赖**：M5-1、M3-8
- **目标**：版权/内容合规校验 + 预算上限；命中合规规则即阻断；超预算自动停并告警。
- **可执行步骤**：
  1. 合规校验前置（§6-6 待确认：业务给版权清单）；命中即阻断采集。
  2. 复用 `crawl_job.budget_limit`：超预算自动停 + 告警。
  3. 来源追溯（可一键下架）。
- **验收（可测）**：命中合规黑名单的源 → 采集被阻断；预算超限 → 自动停 + 告警。
- **涉及**：合规校验、`crawl_job.budget_limit`。
- **波次/估点**：W15 / ≈4d
- **状态**：✅ 已完成：`COMPLIANCE_BLOCKLIST` 默认放行、命中即阻断；`budget_limit` 超限立即停并写 `budget_stop/stopped`；采集行带 source 追溯，run 级一键回退（已审核内容保留）。**【2026-11-11 生产实测】** 黑名单源「禁止转载」创建即 `ValueError` 阻断；`budget_limit=1` 喂 4 题 → `stopped / budget_stop=True`、仅 1 题进 allowed，卷不会被截成半张；`rollback_run` 事务内返回 `removed_questions=2 / removed_papers=1 / intake_rows=1` 且只删目标 run 数据（P2 的 2 道题保留），验证后 rollback。

## 卡 M5-6｜采集可观测
- **归口**：web-backend（日志/指标）
- **依赖**：M5-1~M5-5
- **目标**：任务日志/成功率/去重率；失败可重试、可追溯。
- **可执行步骤**：
  1. 采集任务运行日志落库（start/end/status/计数）。
  2. 指标：成功率、去重率、失败原因分布。
  3. 失败任务可手动重试。
- **验收（可测）**：跑一个采集 → 日志与指标可见；手动触发重试成功。
- **涉及**：采集日志、指标 API。
- **波次/估点**：W15 / ≈3d
- **状态**：✅ 已完成：`GET /admin/course/metrics` 返回成功率/去重率/失败与超预算计数 + 最近运行日志；`POST /courses/jobs/{id}/retry` 手动重试，失败原因可追溯。**【2026-11-11 生产实测】** `crawl_run` 0→5（P1 ok / P2 ok / 重跑 ok / 预算 stopped / 重试 stopped），`run_metrics` → `success_rate=0.6`、`dedup_rate=0.4286`、`recent` 5 条；重跑同批 `duplicated=2`，预算耗尽重试被拒并写 `error` 可追溯。

---

## 附：执行顺序与首期 MVP 对齐（方案书 §9）

- **先钉地基（不依赖待确认项，今天可开工）**：M2-1/2 等价判定纯函数 + 单测 → M2-8 gold set → M0-1 录题（顺带补高三数学选择题量）。
- **首期 MVP（高三·数学·纯选择题卷）**：M0-1+M0-3+M0-4 → M1-1~M1-9（题型先只 `pure_choice`）→ M2-1/2/4/5/6/7/8/9/10/12 → M4-1+M4-2（收紧 `grade=高三,subject=数学,paper_type=pure_choice`）。
- **后置（不在首期）**：M2-3 填空判定（MVP 跑通后紧随，下一卷型=纯填空卷）、M3 全系列、M3-8 定时采集、M4-3/4/7、M5 全系列；跨年级段/跨学科/纯填空卷/混合卷；解答题人工评定流程（首期不进解答题）。
- **三大答案质量闸门（G1/G2/G3）与判分改造同等优先级，不可后置**（§8 硬约束）。
- **外部阻塞（主计划红线）**：ws workspace 预算耗尽→429（LLM/embedding 双挂，恢复靠充值）；`course_kb` 租户隔离（M0-4）；题源缺口（高一/高二 0 题、填空仅 21、难度 1 仅 3、教师题 0）属运营工程，需单独立项。
