# 后端管理菜单「接口 × 角色数据范围」一致性审计

> 生成日期：2026-11-14
> 范围：后端管理后台（网关 `/api/admin/`）各菜单列表接口的数据权限口径。
> 学生端 / App 接口（`learning.*`、`student_learning.*`、`exam_learning.*`、错题本同步等）按登录学生身份隔离，**不适用**本表（单列于末尾）。
> 角色矩阵目标：`admin=全量 / region_admin=本区域 / school|teacher=本租户(只读或受限写)`。

## 0. 审计口径（判定依据）

租户取值三件套（`app/core/security.py`）：

| 函数 | 平台级(admin/region_admin) | teacher/school | 设计语义 |
|---|---|---|---|
| `read_tenant_id` | `None` → 跨租户只读（列表**必须**「None 不加 where」） | 本租户 | 列表只读视图 |
| `business_tenant_id` | `FALLBACK=school_pilot_01`（**绝不 None**） | 本租户 | 既有 service 层 `col == tenant_id` |
| `write_tenant_id` | 本租户（占位原样） | 本租户 | 写侧严格隔离 |
| `_tenant_of`(questions/student_profile) | `FALLBACK=school_pilot_01` | 本租户 | 等同 business 口径 |

**关键结论**：`business_tenant_id` / `_tenant_of` 对 `region_admin`（其 `tenant_id=None`）会**压成 pilot 兜底租户**，而 pilot 属 `rgn_central`——与 region_admin 真实区域（如 `rgn_east`）不同。因此 region_admin 在这类接口里**既漏看本区、又误看异区**。这是本轮"该放宽"的主线。

## 1. 对照表（管理后台菜单接口）

| 菜单/模块 | 接口 | 角色门禁 | 当前数据作用域 | 定性 | 建议 |
|---|---|---|---|---|---|
| 租户管理 | `GET /tenant` | ADMIN,REGION_ADMIN,SCHOOL | `resolve_scope`：region→本区，school→本租户 | ✅ 已对齐 | — |
| 区域管理 | `GET /region` | ADMIN,REGION_ADMIN | region_id 过滤 | ✅ 已对齐 | — |
| 用户管理 | `GET /user` | ADMIN,REGION_ADMIN,SCHOOL,TEACHER | 按登录账号真实 `sys_user` 归属过滤（2026-11-14 已修） | ✅ 已对齐 | — |
| 菜单管理 | `GET /menu`、`/menu/my` | ADMIN / 登录态 | 平台配置 / 身份相关 | 🟦 平台级 | admin-only，无需对齐 |
| 角色管理 | `GET /role` | ADMIN | 平台配置 | 🟦 平台级 | admin-only |
| 数据字典 | `GET /dict`、`/dict/map` | ADMIN | 平台配置 | 🟦 平台级 | admin-only |
| 合规 | `GET /compliance` | ADMIN,REGION_ADMIN,TEACHER,SCHOOL | `read_tenant_id`：平台级跨租户只读，其余本租户 | ✅ 已对齐（有意） | — |
| 合规 | `POST /compliance`、审核 | 同上 | `write_tenant_id` + 跨租户 403 | ✅ 已对齐 | — |
| 错题本 | `GET /wrong-questions` | ADMIN,TEACHER,PARENT,REGION_ADMIN | `read_tenant_id`：平台级跨租户只读，其余本租户 | ✅ 已对齐（有意） | — |
| 错题本 | `POST /wrong-questions` | ADMIN,TEACHER | `business_tenant_id` | ✅ 已对齐 | — |
| 学情看板 | `GET /analytics/summary` | ADMIN,TEACHER,PARENT,REGION_ADMIN | **无租户过滤**，任意 `student_id` 入参（当前返回占位假数据） | ⚠️ 待收紧 | 接真实数据前必须加归属校验，否则 teacher 可查任意学生 |
| 错因分布 | `GET /analytics/error-distribution` | ADMIN,TEACHER,REGION_ADMIN | `scope_tenant`：平台级可跨/可指定；其余本租户且**忽略入参** | ✅ 已对齐 | — |
| 判分待办/指标/护栏/告警 | `GET /grading/tickets,/metrics,/freezes,/alerts` | TEACHER,ADMIN,SCHOOL（**不含** region_admin） | `business_tenant_id` 本租户 | 🟩 安全 | region_admin 被门禁挡掉(403)，无越权；admin 只见 pilot(兜底)——可选放宽 admin 全局视角 |
| 试卷管理 | `GET /exam/papers` | TEACHER,ADMIN,SCHOOL,**REGION_ADMIN** | `business_tenant_id` → region_admin 被压成 **pilot(异区)** | 🔴 待放宽 | region_admin 应按 `region_id` 看全区；当前漏本区+误看异区 |
| 题库审核列表 | `GET /exam/questions` | 同上(ExamManageDep) | `read_tenant_id` → 平台级跨租户只读 | ✅ 已对齐 | 但与 `/papers` 的 region_admin 行为不一致，需在统一方案下定调 |
| 题库审核写 | `POST /exam/questions/{id}/review` | 同上 | `_tenant_of` 严格单租户 404 | ✅ 已对齐 | — |
| 试卷详情/校验 | `GET /exam/papers/{id}`、`/answer-checks`、`/validation` | 同上 | 单租户 `_paper_or_404` | ✅ 已对齐 | — |
| 教师录题列表 | `GET /questions`、`/questions/search` | ADMIN,TEACHER,**REGION_ADMIN** | `_tenant_of` → region_admin 压成 pilot | 🔴 待放宽 | 同试卷：region_admin 应按 region 看全区 |
| 教师录题写 | `POST /questions`、`/batch` | 同上 | `_tenant_of` 写 | ✅ 已对齐 | — |
| 知识点 | `GET /knowledge` | ADMIN,TEACHER,REGION_ADMIN | **无租户过滤（全量）** | ⚪ 待定 | 若为共享课程 DAG 则合理；若要求严格隔离需加租户列与过滤 |
| 学情周报 | `GET /reports/weekly`、`/weekly/page` | ADMIN,TEACHER,PARENT | **透传入参 `student_id`+`tenant_id` 未校验归属** | 🔴 待收紧 | teacher/parent 可指定别校别生读周报；应强制 scope 或仅 admin 可指定 |
| P2 引导质量 | `GET /metrics` | ADMIN,TEACHER | **透传入参 `student_id`+`tenant_id`+`subject`** | 🔴 待收紧 | 同上，需强制归属 |
| LLM 用量 | `GET /llm_usage`、`/llm_usage/budget` | ADMIN,TEACHER | **无租户过滤（全局）** | 🔴 待收紧 | teacher 可见全平台 LLM 成本；应限本租户或仅 admin |
| 学生档案 | `GET /students/profiles` | TEACHER,ADMIN,SCHOOL,**REGION_ADMIN** | `_tenant_of` → region_admin 压成 pilot | 🔴 待放宽 | region_admin 应按 region 看全区学生 |
| BYOM 配置 | `GET /byom/configs` | 任意登录 | admin/region_admin 看全部；school 仅本租户 | ✅ 已对齐（正确建模） | — |
| BYOM 配置 | `GET/PUT /byom/config` | 本租户上下文 | 仅本租户 | ✅ 已对齐 | — |
| 供应商 | `GET /vendor` | ADMIN | 平台配置 | 🟦 平台级 | admin-only |

## 2. 定性结论：哪些该收紧 / 哪些该放宽

### 🔴 该收紧（越权读取，安全优先 P0）
1. **`/reports/weekly`、`/reports/weekly/page`**：teacher/parent 可传任意 `tenant_id`+`student_id`，读别校别生周报。
2. **`/metrics`（P2 指标）**：teacher 可传任意 `tenant_id`+`student_id`+`subject` 聚合。
3. **`/llm_usage`、`/llm_usage/budget`**：无任何租户过滤，返回全平台用量/成本，teacher 可见。
4. **`/analytics/summary`**：占位假数据暂无害，但接入真实学情前必须加归属校验，否则任意 `student_id` 泄露。

> 共性根因：这些接口把**调用方传入的 `tenant_id`/`student_id` 查询参数直接透传**给聚合函数，未与调用者自身 scope 校验。统一修法：teacher/school/parent 强制用 `read_tenant_id(user)`，忽略入参；仅平台级角色允许指定。

### 🔴 该放宽（区域管理员被错位压成 pilot，P1）
5. **`/exam/papers`**、**`/questions`、`/questions/search`**、**`/students/profiles`**：region_admin 经 `business_tenant_id`/`_tenant_of` 被归一到 `school_pilot_01`（属 `rgn_central`），而 region_admin 真实区域是 `rgn_east` → **既漏看本区学校、又误看异区 pilot**。
   - 修法：region_admin 改用 `resolve_scope` 拿 `region_id`，列表 `WHERE tenant_id IN (本区所有租户)`；admin 仍全量，teacher/school 仍本租户。
   - 注意 `/exam/questions`(审核列表) 用的是 `read_tenant_id`（region_admin 跨租户全量），与 `/papers` 行为不一致——统一方案时需二选一定调。

### ✅ 已对齐（有意平台级跨租户只读，保留）
6. **`/compliance`**、**`/wrong-questions`**、**`/analytics/error-distribution`**：统一用 `read_tenant_id` —— 平台级跨租户只读、其余本租户、teacher 忽略入参。这是"运营只读视图"的正确范式，建议其它只读列表对齐它。

### 🟦 平台级配置（admin-only，无需对齐）
7. 菜单、角色、数据字典、供应商、BYOM —— 仅 admin/按钮码可操作，无租户越权面。

### ⚪ 待定
8. **`/knowledge`** 全量无过滤：若知识点是共享课程 DAG（类教材）可接受；若产品要求"某校只看本校知识点"则需加 `tenant_id` 列与过滤。

## 3. 学生端 / App 接口（不适用，仅登记）

`app/api/learning.py`、`student_learning.py`、`exam_learning.py`、错题本同步、试卷作答/提交（student 路由）—— 均以 `X-User-Id`/学生登录态为唯一隔离边界，属"按学生隔离"而非"按角色数据范围"，不在本次菜单权限范围内。唯一红线：`student_mastery.knowledge_point` 落 DAG code、服务端改错题字段必须提 `updated_at`（既有约束，未见回归）。

## 4. 修复优先级建议

- **P0（安全越权）**：reports / metrics(p2) / llm_usage 三处透传与无过滤 → 强制 scope。
- **P1（区域管理员错位）**：exam/papers、questions、student_profile 的 region_admin 改按 region 看全区。
- **P2（一致性/口径）**：analytics/summary 接真实数据前加校验；knowledge 是否共享定调；统一只读列表对齐 `read_tenant_id` 范式。

---

## 5. 实施状态（2026-11-14 已落地）

用户确认按 P0→P1→P2 推进，已全部实现并通过全量后端测试（**743 passed / 0 failed**）。

- **新增 `app/services/scope.py`**：`resolve_read_scope(user, db)` 返回
  `{mode:'all'|'tenant'|'tenants'}` + `where_tenant(stmt, col, rs)` 统一下推；
  `resolve_scope` 改为 DB 行(sys_user) 租户/region 为权威（网关头仅兜底）。
- **P0 已修**：`reports.weekly`(+page)、`metrics`(p2) 非平台强制本租户忽略入参；
  `llm_usage`(+budget) 收紧为 ADMIN-only。
- **P1 已修**：`exam.list_papers`/`list_review_questions`、`questions.list/search`、
  `student_profile` 列表改用 `resolve_read_scope` ⇒ region_admin 看本区、其余看本租户。
- **P2 已修**：`wrong_questions.list_wrong`、`compliance.list_items` 同样改用
  `resolve_read_scope`（region_admin 由"跨全量"收敛为"本区"）。
- **残留（决策待定，未改）**：`/analytics/error-distribution` 的 `scope_tenant` 仍让
  region_admin 跨租户全量（有意设计）；`/analytics/summary` 仍是占位假数据；
  `/knowledge` 全量无过滤（共享课程 DAG 假设）。
- 新增测试 `tests/test_data_scope_lists.py`（9 用例）；修 4 处老测试以匹配新行为。
