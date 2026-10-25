# 学生端 App 五大功能页落地计划书

> **范围**：student-app 底部导航六个 tab 中，除「输入」（已完整实现）与「图谱」（T-B5 已落地）外的功能页 —— **拍照识题 / 讲解展示 / 触类旁通 / 背诵卡·错题本**，外加「图谱」与本期的联动补强。
> **依据**：2026-10-27 代码实测（`student-app` 源码 + agent/web 后端端点 + 137 线上 healthz），非规划态描述。
> **配套文档**：《学生Agent与学生端应用任务计划书》v2（T-AP 系列）、《学生端能力扩展计划书》（T-S/T-F/T-A 系列）、《总任务清单与排期计划》v4.4。
> **估点口径**：小=0.5d、小–中=0.75d、中=1d、中–高=1.5d、高=2d（单人单线程）。
>
> **进度（2026-10-27 更新）**：**波 1 已交付并上线**（T-S1~S5 触类全链 + T-G1/G2 图谱联动，App 单测 **20 绿**、agent-backend **229 绿**）；拍照技术选型已拍板为 **MVP 系统相机 Intent**（CameraX 增强延后）；剩余波 2/波 3 内容不变。详见 §八「波 1 落地记录」。

---

## 一、现状盘点（2026-10-27 实测）

### 1.1 六个 tab 的真实状态

| tab | 路由 | 前端文件 | 状态 | 后端端点 | 可用性 |
|---|---|---|---|---|---|
| 输入 | `input` | `ui/screens/Screens.kt::InputScreen` | ✅ **已完整**（解题 + 判分 + Hint 阶梯 + 直接看解析） | `POST /api/app/solve` | ✅ 通 |
| 拍照 | `camera` | 同上 `::CameraScreen` | ⬜ **EmptyState 占位** | `POST /api/app/solve_image` → `orchestrator.handle_image` → `ocr.recognize` | ⚠️ 代码在，**VLM 未配置** |
| 讲解 | `explain` | 同上 `::ExplainScreen` | ⬜ **EmptyState 占位** | 复用 `/api/app/solve` 的 `answer`/`judgement` | ✅ 数据有，前端未做 |
| 触类 | `similar` | 同上 `::SimilarScreen` | ⬜ **EmptyState 占位** | `POST /api/app/similar`（`mode="similar"`） | ✅ 通，**App 未封装** |
| 图谱 | `graph` | `ui/screens/GraphScreen.kt` | ✅ **已落地**（Canvas DAG + 掌握度 + ZPD） | `GET /learning/knowledge-graph` | ✅ 通 |
| 背诵 | `review` | 同上 `::ReviewScreen` | ⬜ **EmptyState 占位**（用户截图即此页） | 错题本 `GET/POST /api/admin/wrong-questions` | ❌ **学生调不到**（admin 前缀） |

### 1.2 三个硬缺口（必须先解决，否则前端做了也跑不通）

1. **拍照：视觉模型未配置。** 137 线上 `healthz` 明确返回 `"vlm":false`。`ocr.recognize()` 在 `vlm_ready()` 为假时直接返回降级文本 `[dev-未配置视觉模型] …`，不报错、也不阻塞主链路——**前端会"成功"但拿到一段提示语**，这是最容易误判为"已可用"的陷阱。
2. **背诵：错题本接口挂错了前缀。** `web-backend/app/api/wrong_questions.py` 的 router 只有 `GET ""` / `POST ""` 两个端点，且 `main.py:168` 以 `prefix="/api/admin"` 挂载 → 学生端（jwt_c 身份）**没有可调用的读接口**。
3. **背诵：背诵卡后端完全不存在。** 全仓 grep `drill_cards` 只命中 App 端 Room 实体，**后端无表、无端点**；Room 实体也只有 `id/title/content/reviewAt` 四个字段，**无 SRS（间隔重复）所需字段**。

### 1.3 App 依赖缺口

当前 `app/build.gradle.kts` 只有 Compose + Retrofit/OkHttp + Room。缺：**相机能力**（无 CameraX）、**图片加载**（无 Coil，错题本/讲解要显图）、**后台任务**（无 WorkManager，定时复习与离线同步要用）。

---

## 二、总体设计

### 2.1 五页信息流（一张图看懂分工）

```
                    ┌──────────── 拍照 tab ────────────┐
                    │ 系统相机拍照 → 压缩 → 上传        │
                    │ /api/app/solve_image              │
                    │   ├─ OCR 抽出题干                 │
                    │   └─ 回填题干，直接进解题链路      │
                    └───────────────┬──────────────────┘
                                    ↓
  ┌────── 输入 tab（已实现）──────┐   题干/作答
  │ 文本输入 → /api/app/solve    │←──────────┘
  │ → answer + judgement + hint  │
  └───────────┬──────────────────┘
              ↓ 完整结果
  ┌──── 讲解 tab（本期）──────────────────────────┐
  │ 分步讲解（course_kb_steps）+ 公式排版 + 折叠   │
  │ + 复盘三问 + 历史讲解回看                      │
  └───────────┬──────────────────────────────────┘
              ↓ 同题延伸
  ┌──── 触类 tab（本期）────────┐   ┌── 图谱 tab（已有，轻补）──┐
  │ /api/app/similar 变式题列表 │   │ 节点 → 跳该知识点练习/背诵 │
  │ 一键"再练这道" → 回输入页   │   └──────────────────────────┘
  └───────────┬─────────────────┘
              ↓ 做错的题自动归档
  ┌──── 背诵 tab（本期）────────────────────────────────┐
  │ 错题本（自动归档）+ 背诵卡（SRS 间隔复习）+ 离线可背  │
  └────────────────────────────────────────────────────┘
```

### 2.2 复用原则（不重复造）

- **拍照不新建解题链路**：OCR 出题干后复用现有 `/solve` 能力，前端把 `recognized_text` 回填到题干输入框，让学生**先看到识别结果再决定**（识别错可手改）。
- **讲解不复制 Input 的结果区**：Input 是"即时轻量"（答案 + 判分卡 + 引导对话），讲解页是"沉淀后的完整呈现"（分步展开 + 公式 + 复盘三问 + 历史回看）。
- **触类不改后端**：`/api/app/similar` 已实现并已有事件埋点，只补 App 侧封装与 UI。
- **背诵先本地后云**：MVP 全本地（Room），离线优先级高于多端同步；云同步留到 P4 家长端/多端诉求明确后再做。

---

## 三、逐页方案

### 3.1 拍照识题（`camera` tab）

**目标形态**：点「拍照」→ 调系统相机 → 拍照后本地压缩预览 → 上传识别 → 展示识别出的题干（可编辑）→ 一键"开始解题"跳到 Input 页并自动提交。

**技术选型（两档，先轻后重）**

| 档 | 方案 | 依赖 | 估点 | 取舍 |
|---|---|---|---|---|
| **MVP（✅ 已选定）** | `ActivityResultContracts.TakePicture` 系统相机 Intent | 零新增依赖 | 0.5d | 无实时取景/边框辅助，但能立刻跑通全链路 |
| 增强 | CameraX（`PreviewView` + `ImageCapture`） | `androidx.camera:*` 4 个包 | 1.5d | 可加取景框/自动对焦/多张连拍 |

**决策（2026-10-27 用户拍板）**：**走 MVP 系统相机 Intent，不做 CameraX**。理由与先前建议一致——拍照体验的关键瓶颈在**识别准确率（VLM）**而非取景 UI；CameraX 降为可选增强项，等试点反馈"取景框是刚需"再做。

**后端前置（阻塞项，需并行启动）**

- 配置 `VLM_BASE_URL` / `VLM_MODEL` / `LLM_API_KEY`，使 `healthz` 的 `vlm` 转 `true`。
- 需确认阿里云 MaaS 是否有可用视觉模型（`qwen-vl*` 系列）及额度——当前 chat/embed 走 ws 端点，**视觉模型额度情况未验证**，这是拍照功能唯一的硬阻塞。
- 配好后必须用真实试卷照片验证识别质量（题干文字 + 公式），而不只是"接口 200"。

**任务拆解**

| 任务 | 内容 | 依赖 | 估点 |
|---|---|---|---|
| T-P1 | 后端 VLM 接线与验证：配置 VLM 三变量 → healthz `vlm:true` → 真实试卷照片识别质量抽检（≥3 张，含公式） | — | 0.5d |
| T-P2 | App 拍照入口：系统相机 Intent + 权限声明（`CAMERA`）+ 拍后预览确认/重拍 | — | 0.5d |
| T-P3 | 图片压缩与上传：长边压到 1600px、JPEG q=85、`MultipartBody` 调 `solveImage` | T-P2 | 0.5d |
| T-P4 | 识别结果页：展示 `recognized_text`（可编辑）+「开始解题」→ 跳 Input 并预填题干 | T-P3 | 0.5d |
| T-P5 | 失败态与降级：VLM 未配/识别为空/网络失败三态文案 + "改用文字输入"兜底 | T-P4 | 0.25d |
| T-P6 | 单测：`CameraViewModel` 状态机（拍照→压缩→上传→识别→跳转）用 fake ApiService 走通 | T-P3 | 0.5d |

**小计 2.75d**（MVP 路径；CameraX 增强另计 1.5d，可延后）

**验收**：真机拍一张含公式的试卷题 → 识别出题干 → 点"开始解题"能拿到完整讲解；VLM 未配时给出明确"请改用文字输入"而不是空白页。

---

### 3.2 讲解展示（`explain` tab）

**目标形态**：把"一次解题的结果"沉淀成可回看的讲解页——**分步展开**（不是一大段文本）、**公式排版**、**复盘三问**、**历史讲解列表**。

**为什么值得独立一页**：库里已有步骤级讲解数据（`course_kb_steps.explanation` / `error_reason`，S2 已生成数学全量 + 物理部分），但 App 现在只展示 `answer` 整段文字 —— **数据在库里，学生看不到分步**。

**产品决策（三条，需确认）**

1. **分步数据的取用**：优先用 `course_kb_steps`（题干匹配知识点 → 取步骤）；匹配不到时回落到把 `answer` 按换行/序号切段（兜底，保底可用）。
2. **公式渲染**：Compose 无原生 LaTeX。MVP 用**轻量内联规则**（上下标 `x^2`/`a_1`、分式 `a/b` 特殊排版、常见符号映射），不引入 WebView/KaTeX；若试点反馈公式是刚需，再评估 `KaTeX in WebView`（成本 +1.5d）。
3. **历史回看**：MVP 只保留本地最近 N 条（Room `explain_history` 表，N=50，超出 FIFO 淘汰），不做云端历史。

**任务拆解**

| 任务 | 内容 | 依赖 | 估点 |
|---|---|---|---|
| T-E1 | 讲解数据契约：确认 `/solve` 响应是否够用（现有 `answer` + `judgement`）；不足则后端补 `steps[]`（`index/title/content/formula/error_reason`） | — | 0.5d |
| T-E2 | `ExplainScreen` 骨架：分步卡片（折叠/展开）+ 复盘三问区 + 顶部题干摘要 | T-E1 | 0.5d |
| T-E3 | 轻量公式排版：`FormulaText` 组件（上下标/分式/根号/常用符号映射）+ 单测 | T-E2 | 0.75d |
| T-E4 | 本地讲解历史：Room `explain_history` + 列表页（题目摘要/时间/正确与否角标） | T-E2 | 0.5d |
| T-E5 | 从 Input 页跳转：解题完成 → "看完整分步讲解"入口 → 带上本次结果 | T-E2 | 0.25d |
| T-E6 | 组件测试：分步渲染 / 折叠态 / 空态 | T-E3 | 0.5d |

**小计 3.0d**（若 T-E1 需要后端补 `steps[]`，可能 +0.5d）

**验收**：任一解答题解题后点"看完整讲解"→ 看到编号分步、公式不再是一坨纯文本、能折回去看题干、退出后能在讲解历史里找到。

---

### 3.3 触类旁通（`similar` tab）

**目标形态**：一题做完 → 给 3~5 道**同型/变式题**（含难度与考点标签）→ 点任一道"再练"直接进 Input 页。

**现状**：后端 `/api/app/similar` 已实现（`orchestrator.handle_question(question, mode="similar")`，且已埋 `similar` 事件）；**App 的 `ApiService` 里没有这个方法**——这是五页里最快能出成果的一项。

**任务拆解**

| 任务 | 内容 | 状态 | 依赖 | 估点 |
|---|---|---|---|---|
| T-S1 | `ApiService` 补 `@POST("/api/app/similar")` + `SimilarRequest` DTO | 🟢 | — | 0.25d |
| T-S2 | `SimilarViewModel`：入参题干 → 拉取 → 列表状态（loading/empty/error/degraded） | 🟢 | T-S1 | 0.25d |
| T-S3 | `SimilarScreen` UI：变式题卡（题干 + 考点 pill）+「再练这道」+「换一批」 | 🟢 | T-S2 | 0.5d |
| T-S4 | 联动：从 Input 解题完成后带 `question` 跳触类；「再练」回填 Input 并清空旧作答 | 🟢 | T-S3 | 0.25d |
| T-S5 | 单测：ViewModel 三态 + 降级透传 + DTO 解析（fake ApiService，11 例） | 🟢 | T-S2 | 0.25d |

**小计 1.5d —— 2026-10-27 全部完成并上线**

> **实测纠偏（重要）**：后端 `SuggestSimilar` 在 LLM 额度耗尽时会静默回落到检索兜底，
> 返回 **HTTP 200 + 截断 40 字的检索片段**（如 `相似题片段：已知集合 $U=…（知识点：集合的概念）`）。
> 这种内容**不是可练的题**，若照原样展示成"变式题 1"，学生点「再练这道」会把残句搬回输入页。
> 本轮已两处修掉：
> 1. **后端**：`SolveResponse` 补 `similar_degraded` 字段——此前 orchestrator 已算出该值，
>    但 Pydantic `response_model` 未声明被**静默过滤**，前端根本拿不到；
>    兜底文案由「变式：…」改为「相似题片段：…」，不再自称完整题目。
> 2. **App**：DTO 接 `similarDegraded`，降级时展示明确提示条并**禁用「再练这道」**，
>    改引导去图谱用「练这个知识点」（那条链路取题库真题，不受 LLM 额度影响）。
>
> 线上实测（2026-10-27）：`POST /api/app/similar` → 200 / 1.7s / `similar_degraded:true`
> —— 即当前**变式题因额度问题处于降级态**，充值或换端点后自动恢复。

**验收**：输入页解完一题 → 触类 tab 能出 ≥3 道变式题（题干非空、考点有值）→ 点"再练"回到输入页且题干已填、作答已清。

> ⚠️ 质量前提：`mode="similar"` 依赖 LLM 生成。当前主端点（`qwen3.8-flash`）有配额波动历史，若返回降级模板，变式题会退化成固定句式——验收时要**肉眼看题干是否真的不同**，不能只看 HTTP 200。

---

### 3.4 知识图谱（`graph` tab）—— 只做联动补强

图谱本身已落地（T-B5：Canvas 分层 DAG、掌握度配色、ZPD 蓝环、拖动缩放、节点详情）。本期**不重做**，只补两处联动，让它从"看得见"变成"用得上"：

| 任务 | 内容 | 状态 | 估点 |
|---|---|---|---|
| T-G1 | 节点详情面板加「练这个知识点」按钮 → 跳 Input（预置知识点上下文，走 ZPD 推题接口时同源） | 🟢 | 0.25d |
| T-G2 | 节点详情面板加「背这个知识点」按钮 → 跳背诵页并按 `knowledge_point` 过滤 | 🟢 | 0.25d |

**小计 0.5d —— 2026-10-27 全部完成并上线**

> **实现口径（与原文略有出入，按实测调整）**：T-G1 最终走的是 **T-A4 的
> `GET /learning/variants?kp=<code>&n=1`** 而不是 `/api/app/similar` 之类的 LLM 生成
> ——图谱要的是"这道题确实属于该知识点"，题库真题带 `content_hash` 可回查，
> 且**不受 LLM 额度影响**（触类页当前正因额度降级，图谱这条链路实测 200 稳定可用）。
> 取不到题时按钮回落为提示「该知识点暂无可练题目（题库未覆盖）」，不跳转。
>
> T-G2 的落点已备好：背诵页本体属波 3，当前占位页会显示"已选定背诵范围：`<标题> · <code>`"，
> 届时直接读同一个 `AppBridge.pendingFocusKp` 即可，无需再改图谱。

> 说明：真正的"按知识点自适应推题"属 T-B3（ZPD 自适应推题，已完成）在后端的落地，App 侧只需带参跳转，不在本期扩范围。

---

### 3.5 背诵卡 / 错题本（`review` tab）—— 本期最重的一块

**目标形态**：两个子 tab——**错题本**（自动归档、按知识点/时间筛选、详情含原题 + 我的作答 + 错因 + 步骤归因）与**背诵卡**（SRS 间隔重复，今天该背的卡片优先，正向/反向翻卡，离线可用）。

**必须先补的三处地基**

1. **错题本 App 侧接口**：新增 `GET /api/app/wrong-questions`（jwt_c，按学生隔离），字段对齐 `wrong_questions` 表（题干/我的答案/正确答案/知识点/错因/首错步/时间）。现有 `/api/admin/wrong-questions` 保持不动（B 端管理用）。
2. **背诵卡后端表**：`drill_cards`（`card_id / student_id / knowledge_point / front / back / interval_days / ease / due_at / lapses / source`），配套 `GET /api/app/drill-cards?due=1`。
3. **Room 实体扩字段**：`DrillCard` 现只有 `id/title/content/reviewAt`，需补 `knowledgePoint / intervalDays / ease / dueAt / lapses / syncedAt`；`WrongQuestion` 补 `errorType / firstWrongStep / correction`。

**SRS 算法（SM-2 简化版，规则先定死避免反复）**

```
答对：interval = if (lapses==0 && reps==0) 1 else if (reps==1) 3 else round(interval * ease)
     ease = min(2.8, ease + 0.1)
答错：interval = 1（次日重来）；ease = max(1.3, ease - 0.2)；lapses += 1
dueAt = now + interval 天（按本地 04:00 切日）
```

**任务拆解**

| 任务 | 内容 | 依赖 | 估点 |
|---|---|---|---|
| T-R1 | 错题本 App 端点：`GET /api/app/wrong-questions`（学生隔离 + 分页 + 按知识点筛选）+ 单测 | — | 0.75d |
| T-R2 | `drill_cards` 建表 SQL + `GET/POST /api/app/drill-cards`（幂等 upsert）+ 单测 | — | 1d |
| T-R3 | Room 实体升级（Version 1→2 迁移）+ `DrillCardDao`/`WrongQuestionDao` 查询（到期/按知识点） | — | 0.5d |
| T-R4 | SRS 纯函数（`SrsScheduler.next()`，Kotlin 纯逻辑）+ JVM 单测（对/错/连续对/连续错四种轨迹） | T-R3 | 0.5d |
| T-R5 | 错题本 UI：列表（知识点/时间筛选、错因角标）+ 详情（原题/我的作答/正确/首错步/纠正） | T-R1 | 1d |
| T-R6 | 背诵卡 UI：今日待背队列、翻卡（正/反）、"记得/忘了"两键、进度与剩余数 | T-R4 | 1d |
| T-R7 | 离线与同步：无网读本地；有网时把本地新增卡片/复习记录回传（失败静默重试，不阻塞 UI） | T-R6 | 0.75d |

**小计 5.5d**

**验收**：
- 做错一道解答题 → 错题本能自动看到（无需手动收藏），点进去看到首错步与错因；
- 背诵页当天有到期卡 → 翻卡 → 点"记得"后该卡明天再来、点"忘了"后次日重来；
- 飞行模式下背诵页仍可用，且复习记录在恢复网络后能回传。

---

## 四、排期建议（三波，可并行）

| 波次 | 内容 | 估点 | 说明 |
|---|---|---|---|
| **波 1 · 快赢** | T-S1~S5（触类全链 1.5d） + T-G1/G2（图谱联动 0.5d） | **2.0d** | ✅ **已完成并上线（2026-10-27）**；触类当前受 LLM 额度影响处降级态，图谱链路不受影响 |
| **波 2 · 地基** | T-P1（VLM 配置验证 0.5d） + T-R1/R2/R3/R4（错题本端点 0.75 + 背诵卡表 1.0 + Room 迁移 0.5 + SRS 纯函数 0.5） | **3.25d** | 补齐三个硬缺口；T-P1 依赖外部（视觉模型额度），**建议立刻并行启动** |
| **波 3 · 页面** | T-P2~P6（拍照 UI 2.25d） + T-E1~E6（讲解页 3.0d） + T-R5/R6/R7（错题本+背诵 UI 2.75d） | **8.0d** | 三页可并行推进；讲解页的 T-E1 若需后端补 `steps[]` 先做 |

**合计 ≈ 13.25d**（2.0 + 3.25 + 8.0；不含 CameraX 增强 1.5d 与 KaTeX 公式渲染 1.5d 两个可选项）
**剩余 ≈ 11.25d**（波 1 已交付）

**下一步建议**：波 2 的 T-P1（VLM 额度）是外部依赖、越早提越好；同时可并行开 T-R1/R2（错题本学生端点 + `drill_cards` 建表），这两个是纯后端、不依赖外部。

---

## 五、依赖与阻塞（非本计划可控）

| 阻塞项 | 影响 | 处置 |
|---|---|---|
| **视觉模型（VLM）额度/可用性未验证** | 拍照页全部（唯一的硬阻塞） | 立即确认阿里云 MaaS 视觉模型可用性与额度；不可用则拍照页降级为"相册选图 + 手动确认题干"，识别交 LLM 兜底 |
| **主 LLM 端点配额波动**（`qwen3.8-flash` 有 403 历史） | 触类变式题、讲解生成质量 | 验收看内容质量而非 HTTP 码；降级时给出明确提示，不静默返回模板 |
| **错题本历史数据** | 背诵页首屏是否有内容 | T2"错题本自动写入链路"已完成，需确认线上已有存量数据可供验收 |
| **无真机** | 拍照页（T-AP4/T-AP8 同因） | 需真机或云真机；模拟器相机不可用于验收 |

---

## 六、风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 拍照识别准确率不达预期（公式/手写） | 高 | 识别结果**必可编辑**，识别差也能手改继续；不做"识别即提交" |
| 背诵卡做好了但学生不用 | 中 | 首页/图谱给"今日待背 3 张"入口；SRS 规则先简单可解释，不追求算法最优 |
| 本地优先导致多端数据不一致 | 中 | 明确 MVP 不做多端同步；Room 加 `syncedAt` 字段为将来留口 |
| 讲解页与 Input 页职责重叠、两边都做一半 | 中 | 边界写死：Input 只做即时轻量，讲解页只做分步沉淀与历史 |
| 四页同时铺开导致哪页都没做完 | 中 | 严格按波次：波 1 完成再进波 3；一次只允许两个页面并行 |

---

## 七、验收标准（整体）

1. **六个 tab 无空页**：不再出现"即将上线"占位（截图状态清零）。
2. **主链路闭环**：拍照/输入 → 讲解（分步）→ 触类（变式）→ 做错 → 错题本自动出现 → 背诵复习 → 图谱掌握度变化。
3. **离线可用**：断网时背诵页与错题本可读可背，不白屏。
4. **降级明确**：VLM 未配、LLM 配额耗尽、网络失败三种情况都有可理解的中文提示与替代路径。
5. **测试**：新增 JVM 单测覆盖 SRS 纯函数、三个 ViewModel 状态机、DTO 解析；`./gradlew :app:testDebugUnitTest` 全绿。
6. **真机验证**：至少一台真机跑通拍照 → 解题 → 讲解 → 触类 → 背诵的完整一圈。

---

## 八、波 1 落地记录（2026-10-27，已上线 137）

### 8.1 交付清单

| 层 | 文件 | 内容 |
|---|---|---|
| 新增 | `app/.../state/AppBridge.kt` | 跨 tab 一次性传参桥接（`lastQuestion` / `pendingQuestion` / `pendingFocusKp`），**取出即清空** |
| 新增 | `app/.../viewmodel/SimilarViewModel.kt` | 触类旁通状态机（三态 + 降级标记 + 空白题干不发请求） |
| 改 | `data/remote/ApiService.kt` | 补 `SimilarRequest` + `POST /api/app/similar`；`VariantsResponse` + `GET learning/variants`；`SolveResponse.similarDegraded` |
| 改 | `ui/screens/Screens.kt` | `SimilarScreen` 实装（来源题干可编辑 / 变式题卡 / 换一批 / 降级提示）；`InputScreen` 消费 `pendingQuestion` 并 `vm.reset()`；`ReviewScreen` 显示已选背诵范围 |
| 改 | `ui/screens/GraphScreen.kt` | `NodeDetailCard` 加「练这个知识点」「背这个知识点」+ 加载/无题提示 |
| 改 | `viewmodel/GraphViewModel.kt` | `practiceKp()`：按 kp 拉真题 → 回调跳输入页；无题/失败只提示不跳转 |
| 改 | `viewmodel/SolveViewModel.kt` | 补 `reset()`（接新题时清掉上一题的答案与判分卡） |
| 改 | `MainActivity.kt` | 抽出 `go(route)` 导航闭包，底部栏与页内联动共用单实例+保状态语义 |
| 后端 | `agent-backend/app/models/schemas.py` | `SolveResponse` 补 `similar_degraded`（此前被 `response_model` 静默过滤） |
| 后端 | `agent-backend/app/core/llm.py` | 兜底文案「变式：…」→「相似题片段：…」，不再自称完整题目 |

### 8.2 实测结论

- **后端**：agent-backend **229 passed**（本地全量）；137 容器重建后 healthz 全绿。
- **App**：JVM 单测 **20 passed / 3 类**（`SolveViewModelTest` 4 + `SimilarViewModelTest` 11 + `AppBridgeTest` 5）；`assembleDebug` BUILD SUCCESSFUL。
- **线上（经 lb）**：
  - `POST /api/app/similar` → **200 / 1.7s**，但 `similar_degraded:true`（ws 端点 `qwen3.8-flash` **403 Free quota exhausted**）——变式题当前是降级片段；
  - `GET /learning/variants?kp=MA-B1-C1-S1-K01&n=1` → **200**，返回题库真题（stem 含 LaTeX：`$U=\\{x\\mid x\\text{是小于 }9...\\}$`），图谱联动链路**不受额度影响**；
  - `GET /learning/knowledge-graph` → 200 / 171 KB。

### 8.3 本轮踩到的四个坑（留给后续）

1. **Pydantic `response_model` 会静默丢弃未声明字段**：orchestrator 早就算好了 `similar_degraded`，因为 `SolveResponse` 没声明它，前端一无所知。以后加"回传标记"必须同时改 model。
2. **`options` / `knowledge_points` 是 jsonb**：不同行可能是数组也可能是字符串，Gson DTO 里声明成 `List<String>` 会在遇到字符串时**整条解析失败**。App 侧只声明要用的标量字段，其余交给 Gson 忽略。
3. **Kotlin 字符串里的 `$`**：题干含 LaTeX（`$U=`）时，单测字符串必须写 `\$U=`，否则编译报 `Unresolved reference: U`。
4. **Compose 跨 tab 传参**：底部导航是独立 `NavBackStackEntry`，改带参路由成本高；用进程内 `StateFlow` 单例 + **一次性消费**更轻，但必须"取出即清空"，否则每次回到该页都被旧值顶掉。

### 8.4 遗留

- 触类页的**实际内容质量**取决于 LLM 额度；当前降级，需充值或换端点后复验（复验标准：变式题题干是**完整可作答**的题目，而非"…（知识点：X）"结尾的片段）。
- 拍照页 T-P1（VLM 额度）仍待外部确认；`vlm:false` 未变。
- 讲解页、背诵页本体未动（波 2/波 3）。

---

*本计划书依据 2026-10-27 实测现状编写；任务编号 T-P/E/S/G/R 为本计划新增，与总表的 T-AP（App 发布/UI 规范/弱网）系列互补不重叠。总量 ≈13.25 人天；**波 1（2.0d）已于 2026-10-27 交付上线，剩余 ≈11.25d**。*
