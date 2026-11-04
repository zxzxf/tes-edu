# 知识图谱学情报告 PDF 方案

> 版本：v1.0（2026-11-04）
> 状态：待评审
> 关联：学生端 App（student-app）、web-backend、网关
> 目标一句话：学生整卷交卷后，后端异步生成一份「知识点掌握度 + 图谱静态图」的 PDF，学生在 App 知识图谱菜单下直接翻看。

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 可行性 | **可行**，全部为增量改动，不碰现有交卷判分与 Canvas 交互图谱 |
| 新增依赖 | 后端 1 个（`reportlab`，纯 Python wheel）；客户端 **0 个**（用系统 `PdfRenderer`） |
| 工作量 | 后端 ≈1d、客户端 ≈0.8d、联调验收 ≈0.5d，合计 **2.3d** |
| 最大风险 | 中文 PDF 字体在 Android `PdfRenderer` 下的渲染效果，需真机验一次（备选方案已给） |
| 需要新增的表 | 1 张 `graph_reports` |
| 需要新增的接口 | 2 个（查状态、下文件），挂在已有 `/api/student/` 前缀下，**网关主配置免改** |

**已确认的产品边界**（本轮拍板）：

1. PDF 内容 = **掌握度报告 + 图谱静态图**；交互式缩放/点节点仍归现有 Canvas，不进 PDF；
2. 触发范围 = **仅整卷交卷**；单题解题在 agent-backend，跨服务成本高，本期不做；
3. 保留策略 = **每个学生每份卷只留最新一份**，不做历史报告列表；
4. 分享 = **本期不做**系统分享/导出，只做 App 内查看。

---

## 1. 现状核实（均为代码实测）

### 1.1 触发侧：交卷

| 事实 | 位置 |
|---|---|
| 整卷交卷接口 `POST /api/student/exams/{assignment_id}/submit` | `web-backend/app/api/exam.py:1278`（`async def submit_exam`） |
| 判分与落库在同一个事务里，末尾 commit | `web-backend/app/api/exam.py:1279`–`1420`（commit 约在 1418 附近） |
| 单题解题走 agent-backend `/api/app/solve`（本期不接） | `web-backend` 之外 |

结论：交卷后**有明确的、事务已提交的挂载点**，在这里触发最自然。

### 1.2 数据侧：报告要用的数据全都现成

| 事实 | 位置 |
|---|---|
| 全量知识图谱（节点/边/汇总，已联掌握度、ZPD、题量） | `web-backend/app/services/graph.py:262` `knowledge_graph_db` |
| 按卷知识图谱（本卷涉及知识点） | `web-backend/app/services/graph.py:173` `paper_graph_db` |
| 掌握度分档（优秀/良好/待提升…） | `web-backend/app/services/graph.py:36` `mastery_band` |
| 知识点元数据批量加载 | `web-backend/app/services/graph.py:104` `load_kp_meta` |
| 练习统计（做对/做错次数） | `web-backend/app/services/graph.py:145` `load_practice_stats` |

结论：**不需要新写一条取数 SQL**，报告服务直接调这两个函数即可。

### 1.3 后端：异步与存储的现状

| 能力 | 现状 |
|---|---|
| PDF 生成 | ❌ 无。`requirements.txt` 里没有任何 PDF/渲染库 |
| 异步任务 | ⚠️ 部分有。有 Kafka 客户端（`app/core/kafka_client.py`）与启动时 `asyncio.create_task`（`main.py` 启动钩子），但**没有"任务表 + 状态轮询"这种面向用户的异步作业机制**（`crawl_job` 是采集任务，不复用） |
| 文件存储/下载 | ❌ 无。没挂 `StaticFiles`，没有对象存储，只有 CSV/HTML 即时响应（`app/api/analytics.py` 的附件写法可抄） |

### 1.4 客户端现状

| 能力 | 现状 |
|---|---|
| 图谱菜单 | 底部导航 Tab（label「图谱」），`ui/screens/Screens.kt` 里组装，跳 `GraphScreen` |
| 图谱渲染 | 纯客户端 Canvas 自绘（`ui/screens/GraphScreen.kt` + `ui/graph/GraphLayout.kt`），服务端只给 JSON |
| 页内 Tab 范式 | ✅ 已有可抄：`Screens.kt` 的 `ReviewTab` |
| PDF 渲染 | ❌ 无 |
| 文件下载 | ❌ 无（Retrofit 目前只有 JSON 接口，没有 `@Streaming` 二进制下载） |
| 后台轮询 | ❌ 无，且 `Screens.kt:1069` 明确写了「不做后台定时轮询」——**本方案必须遵守，只在页面可见时轮询** |
| minSdk | 24（`app/build.gradle:15`）→ 系统 `PdfRenderer`（API 21+）可用 |
| PDF 相关依赖 | ❌ 无，也不打算加 |

---

## 2. 总体架构

```
┌──────────────┐   ①交卷 POST /submit
│ student-app  │───────────────────────────────► web-backend（exam.py）
└──────────────┘                                      │ ②判分落库 commit
       │                                              │ ③INSERT graph_reports(status=pending)
       │                                              │ ④asyncio.create_task 后台生成
       │                                              ▼
       │                                       graph_report.py
       │                                   取数(paper_graph_db) → reportlab → PDF
       │                                              │ ⑤UPDATE pdf_bytes / status=ready
       │                                              ▼
       │   ⑥进入图谱页 GET /reports/{assignment_id}   PostgreSQL(graph_reports)
       └──────────────────────────────────────────────┘
                        │ status=ready
       ⑦ GET /reports/{assignment_id}/file → application/pdf → cacheDir
       ⑧ PdfRenderer 逐页渲 Bitmap → Compose 里翻页看
```

关键点：**生成是异步的，客户端永远拿不到"生成中"的 30s 长连接**，因此绕开了 nginx 30s 超时。

---

## 3. 关键设计决策

| # | 决策 | 理由 | 备选/放弃原因 |
|---|---|---|---|
| D1 | 生成逻辑放 **web-backend** | 掌握度数据、图谱服务、交卷入口都在这里，零跨服务调用 | 放 agent-backend 要跨服务读库 + 跨服务回调，收益为零 |
| D2 | 触发用 **`asyncio.create_task`** | 单实例部署，量极小（每生每卷一次），最简单 | Kafka：抗重启、可水平扩展，但当前无消费组承载 PDF 渲染，属于过度设计；**若未来 web-backend 多副本，切 Kafka 是平滑的**（把 create_task 换成 produce 即可） |
| D3 | PDF 存 **DB `bytea`** | 单文件 100~300KB，量级极小；避免多副本/重启后文件丢失、避免引入对象存储 | 落本地磁盘：容器重建即丢；OSS：新增云资源与密钥 |
| D4 | 中文用 reportlab 内置 **`STSong-Light`**（CID 字体，不嵌字体文件） | 零字体文件、零镜像体积增加 | 内嵌 TTF 子集：更稳但仓库要放 3MB 字体且需子集化 |
| D5 | 客户端用 **系统 `PdfRenderer`** | 零新依赖、离线可看、渲染在本地不耗流量 | WebView 打 PDF：Android 原生 WebView 不支持 PDF；跳第三方阅读器：不满足「菜单下直接显示」 |
| D6 | 轮询**仅页面可见时**进行 | 遵守既有「不做后台轮询」约定 | 前台 Service/WorkManager：违反约定且没必要 |
| D7 | 幂等键 `(student_id, assignment_id)` **唯一** | 重复交卷/重试只覆盖，不产生垃圾 | 每次新建行：需额外清理逻辑 |
| D8 | 交卷 **commit 之后**才触发 | 后台任务读不到未提交事务 | 事务内触发：几乎必然生成空报告 |

---

## 4. 数据模型

### 4.1 表 `graph_reports`

```sql
CREATE TABLE IF NOT EXISTS graph_reports (
    id            SERIAL PRIMARY KEY,
    student_id    VARCHAR(64) NOT NULL,
    assignment_id INTEGER     NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/generating/ready/failed
    pdf_bytes     BYTEA,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_graph_reports_student_assignment UNIQUE (student_id, assignment_id)
);
CREATE INDEX ix_graph_reports_student_id    ON graph_reports (student_id);
CREATE INDEX ix_graph_reports_assignment_id ON graph_reports (assignment_id);
```

落库方式：新增 `migrations/0018_graph_reports.sql`（沿用现有迁移约定，编号接 0017），同时在建表函数里纳入 `create_all`，保证新环境自动建表。

### 4.2 状态机

```
pending ──(worker 取到)──► generating ──成功──► ready
                              └────失败────► failed ──(重试)──► pending
```

- `ready` 是唯一可下载状态；
- `failed` 保留 `error` 摘要（截断 500 字），供客户端展示"生成失败，请重试"；
- `generating` 超过 2 分钟视为僵死，查询时降级为 `failed`（防 worker 被杀导致永远转圈）。

---

## 5. 接口契约

统一挂在 `/api/student/` 前缀下（网关 `gateway/config.yaml:51` 已有该路由，`mode: jwt_c`），**主网关配置无需改动**。

### 5.1 查询状态

```
GET /api/student/reports/{assignment_id}
Authorization: Bearer <student jwt>
```

响应 200：

```json
{
  "status": "ready",
  "assignment_id": 1024,
  "assignment_title": "高一数学·函数与导数单元测",
  "created_at": "2026-11-04T18:40:12+08:00",
  "updated_at": "2026-11-04T18:40:15+08:00",
  "size_bytes": 187342,
  "error": null
}
```

- 无记录返回 `{"status": "none"}`（200，不用 404，省得客户端把 404 当网络错误）；
- **越权防护**：`student_id` 一律从 JWT 取，不接受客户端传参；查库时带 `student_id` 条件。

### 5.2 下载文件

```
GET /api/student/reports/{assignment_id}/file
```

- 200 → `Content-Type: application/pdf`、`Content-Disposition: inline; filename="graph-report-1024.pdf"`、`Content-Length`；
- 非 `ready` → 409 + `{"detail": "报告尚未生成完成"}`；
- 鉴权同上，且校验该报告属于当前学生。

### 5.3 手动重试（可选，建议保留）

```
POST /api/student/reports/{assignment_id}/retry
```

仅当状态为 `failed`（或 `none`）时允许，重置为 `pending` 并重新投递后台任务。

---

## 6. PDF 版式设计

单文件 A4 纵向，5 页封顶（知识点特别多的卷子附录可续页），页脚统一「TES · 知识图谱学情报告 / 第 N 页」。

| 页 | 内容 | 数据来源 |
|---|---|---|
| P1 总览 | 学生名、卷名、提交时间、**总体掌握度环形/大字**、统计条（本卷知识点数 / 已掌握 / 待提升 / 本次正确率） | `paper_graph_db` 的 `summary` + 交卷结果 |
| P2 章节掌握度 | 横向条形图（按章节聚合掌握度，0–100），每行标注题量与分档色 | `assemble_node` + `mastery_band` |
| P3 知识点图谱 | **静态图**：圆点=知识点（大小=题量、颜色=分档），线=前置/关联；章节分组浅色底块 + 标题 | `paper_graph_db` 的 `nodes`/`edges`，服务端用 reportlab 画布绘制 |
| P4 薄弱项与建议 | Top 5 薄弱知识点（掌握度最低且题量≥阈值），每条：名称、掌握度、错题数、一句建议；有前置缺失的标「建议先补：X」 | 节点字段 + `kp_prerequisites` |
| P5 附录 | 全部涉及知识点清单表：知识点 / 章节 / 掌握度 / 题量 / 对错数 / 分档 | 全量节点 |

样式规定（保证打印清晰）：主色沿用 Web 端设计系统（深蓝 `#1B3A57` 主色、`#E8A33D` 强调），正文 10.5pt，标题 16/13pt，表格线 0.5pt 浅灰。

---

## 7. 后端实现拆解

### 7.1 新增文件

| 文件 | 职责 |
|---|---|
| `web-backend/app/services/graph_report.py` | 取数 → 渲染 PDF → 回写。对外只暴露 `generate_report(db, student_id, assignment_id)` |
| `web-backend/app/api/student_reports.py` | 三个接口（状态/文件/重试），`APIRouter(prefix="/reports", tags=["student-reports"])` |
| `web-backend/migrations/0018_graph_reports.sql` | 建表 + 索引 |

### 7.2 修改文件

| 文件 | 改动 |
|---|---|
| `web-backend/requirements.txt` | 加 `reportlab==4.2.2`（钉版本，纯 wheel，slim 镜像不需要 apt） |
| `web-backend/app/models/ops.py` | 追加 `GraphReport` 模型（与本文件既有模型同风格） |
| `web-backend/main.py` | `include_router(student_reports.router, prefix="/api/student")` |
| `web-backend/app/api/exam.py` | `submit_exam` 在 **commit 之后**：upsert 一行 `pending` + `asyncio.create_task(_run_report(...))` |

### 7.3 后台任务要点（踩坑清单）

1. **不能用 request 的 session**：`create_task` 里必须新开独立 session（`async_sessionmaker()`），否则请求结束 session 关闭，任务报 `Session is closed`；
2. **全包 try/except**：任务里任何异常都要 catch，写 `status=failed, error=str(e)[:500]`，否则异常逃逸到事件循环只留一条日志，客户端永远转圈；
3. **触发点在 commit 之后**，且 `create_task` 要 `await` 一次让出控制权前先落 `pending` 行；
4. **不要在事务里 `await` 生成**，否则交卷响应被拖慢；
5. 生成前 `sleep 3s`（见 8.1 时序坑）；
6. 生成耗时实测预估 0.3~1.5s（纯本地绘制 + 100~300KB 输出），但**仍走异步**，防负载抖动撞 30s。

---

## 8. 三个必须提前处理的坑

### 8.1 时序坑（最重要）：交卷瞬间掌握度还是旧的

掌握度**不是交卷时同步更新**的——agent-backend 通过 Kafka `student_event` 异步写 `student_mastery`。如果 commit 后立刻生成，PDF 里的掌握度可能是**上一轮**的值，学生一看「我刚交卷怎么没变」。

处理（二选一，建议 A）：

- **A（推荐）**：后台任务先 `sleep 3s` 再取数，并在 PDF 页脚/首页标注「数据截至 <时间>」——多数情况下 3s 足够 Kafka 链路落库；
- B：把触发点从「交卷 commit」改成「Kafka 消费到该次提交事件之后」。更准，但要改 agent-backend 消费侧，成本高，本期不做。

### 8.2 超时坑：必须异步

`deploy/nginx.conf:31` 全局 `proxy_read_timeout 30s`（仅 `/api/app/` 放到 180s）。同步生成 PDF 一旦超过 30s 就是 504，且用户会重试、雪崩。**异步 + 短轮询是唯一稳的解法**。

### 8.3 网关旧副本坑

`gateway/config.yaml:51` 有 `/api/student/` 路由，但仓库里另一份 `deploy/gateway-config.yaml` **没有**（实测 grep 计数 0）。137 上若用的是后者，新接口会 404。上线时两份要同步（本次只是补齐，不改语义）。

---

## 9. 客户端实现拆解

### 9.1 新增

| 文件 | 职责 |
|---|---|
| `data/remote/ReportApi.kt` | `GET reports/{id}` 返回状态；`@Streaming GET reports/{id}/file` 返回 `ResponseBody` |
| `data/model/ReportModels.kt` | `ReportStatusDto` 等 |
| `ui/pdf/PdfReportView.kt` | 用 `PdfRenderer` 把 PDF 每页渲成 `Bitmap`，`LazyColumn` 翻页；加载中/失败/空态 |

### 9.2 修改

| 文件 | 改动 |
|---|---|
| `viewmodel/GraphViewModel.kt` | 新增 `reportState: StateFlow<ReportUiState>`（`None/Loading/Generating/Failed/Ready(bitmap页数)`）+ `refreshReport()` + `ensureReportFile()`；**页面不可见时停轮询** |
| `ui/screens/GraphScreen.kt` | 顶部加页内 Tab：「图谱 / 学情报告」，`REPORT` 页承载 `PdfReportView`；仿 `Screens.kt` 的 `ReviewTab` 写法 |

### 9.3 渲染细节

1. `PdfRenderer` 只接受 **seekable 文件**：先下载到 `context.cacheDir/reports/{assignment_id}.pdf`，再用 `ParcelFileDescriptor.open`；
2. 逐页渲染，**只保留当前页 ±1 的 Bitmap**，离开页面即 `close()` + `recycle()`，防 OOM（A4 单页 @2x ≈ 4~8MB）；
3. 按 `1440x2036` 之类的目标宽度等比缩放，避免大图爆内存；
4. 文件缓存：响应里带 `updated_at`，与本地记录一致则**直接复用文件不发请求**（省流量，避免每次进页面重下）；
5. 生成中 UX：`Generating` 态显示「报告生成中…（约 3~5 秒）」+ 2s 间隔、最多 15 次的页内轮询；超时给「稍后重试」按钮，不弹错误框；
6. 若 `PdfRenderer` 抛 `IOException`（文件损坏），删缓存重下一次，二次失败才报错。

---

## 10. 工作量与里程碑

| 里程碑 | 内容 | 交付物 | 估时 |
|---|---|---|---|
| M1 | 后端：建表 + PDF 服务 + 三接口 + 交卷触发 | 可用 curl 拉到一个真 PDF | 1.0d |
| M2 | 客户端：接口 + ViewModel + `PdfReportView` + Tab | 真机能在图谱页看到报告 | 0.8d |
| M3 | 联调 + 真机验收（重点验中文渲染与内存） | 验收清单全绿 | 0.5d |
| M4 | 部署（137 重建 web-backend、同步网关配置） | 线上可用 | 0.2d |

---

## 11. 验收清单

后端

- [ ] 交卷后 3~5s 内 `GET /reports/{id}` 由 `pending` 变 `ready`
- [ ] 重复交卷同一份卷，只有一行记录、PDF 被覆盖更新（不产生第二行）
- [ ] `GET /file` 返回的 PDF 能被本地阅读器正常打开、中文不乱码
- [ ] 换一个学生 token 请求同一 `assignment_id`，返回 403/无数据（越权拦截生效）
- [ ] 生成异常时 `status=failed` 且 `error` 有摘要，客户端不卡转圈
- [ ] 未交卷的卷请求报告 → `{"status":"none"}`

客户端

- [ ] 交卷后直接进「图谱 → 学情报告」能在 10s 内看到报告
- [ ] 断网进入：显示「加载失败 + 重试」，不崩不卡
- [ ] 连续翻 5 页不 OOM（`adb shell dumpsys meminfo` 观察峰值 < 200MB）
- [ ] 中文标题/知识点名清晰无方框、无重叠
- [ ] 退出页面再进入：命中文件缓存，秒开且不再请求
- [ ] 切到别的 Tab 后不再有网络请求（轮询已停）

---

## 12. 风险登记

| 风险 | 等级 | 影响 | 应对 |
|---|---|---|---|
| `STSong-Light` 在 Android `PdfRenderer` 下显示异常（方块/缺字） | **高** | PDF 不可读，功能废掉 | M1 阶段先出样例 PDF，**在真机 `PdfRenderer` 里先验一次**再往下做；不行则切内嵌思源黑体子集（`NotoSansSC-Regular` 子集 ~1.5MB，放 `web-backend/assets/`） |
| 掌握度时序导致报告数字"不对" | 中 | 用户信任度 | `sleep 3s` + 报告标注数据时间；必要时改 Kafka 触发（8.1-B） |
| `bytea` 存储随学生数增长 | 低 | DB 体积 | 每生每卷仅一行、唯一键覆盖；一年万级学生 ≈ 数 GB，可接受；后续可加"只保留最近 N 卷"清理 |
| `create_task` 在容器重启时丢任务 | 低 | 个别报告永远 `pending` | `generating` 超 2 分钟判定失败 + 客户端「重试」按钮兜底 |
| 网关旧副本缺路由 | 低 | 137 上 404 | 部署检查项（8.3） |

---

## 13. 涉及文件总清单

**新增（5）**：`web-backend/app/services/graph_report.py`、`web-backend/app/api/student_reports.py`、`web-backend/migrations/0018_graph_reports.sql`、`student-app/.../data/remote/ReportApi.kt`、`student-app/.../ui/pdf/PdfReportView.kt`

**修改（7）**：`web-backend/requirements.txt`、`web-backend/app/models/ops.py`、`web-backend/main.py`、`web-backend/app/api/exam.py`、`student-app/.../viewmodel/GraphViewModel.kt`、`student-app/.../ui/screens/GraphScreen.kt`、`deploy/gateway-config.yaml`（补 `/api/student/`）

**不动**：现有 Canvas 图谱渲染（`GraphScreen` 图形部分 / `GraphLayout.kt`）、交卷判分逻辑、agent-backend 全部代码。

---

## 14. 待确认

1. 掌握度时序：接受「交卷后 3s 快照 + 报告标注数据时间」吗？（否则要走 Kafka 触发，工期 +0.5d）
2. 报告是否需要显示「本次正确率 / 得分」这类判分结果？（会用到交卷返回结果，属增量但需从 `exam.py` 传下来）
3. PDF 里的学生姓名/卷名，是否要求脱敏（只显示昵称首字）？
