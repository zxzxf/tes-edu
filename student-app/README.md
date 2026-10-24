# 学生端 App（student-app）

Android 学生端（C 端核心产品），技术栈 **Kotlin + Jetpack Compose**。

## 技术栈

- **Kotlin + Jetpack Compose**：声明式 UI
- **ViewModel**：状态管理与意图分发
- **Retrofit**：网络请求，对接 Go 网关
- **Room**：本地缓存（背诵卡 / 错题本），离线可用
- **导航**：底部 6 个能力页

## 目录结构

```
app/src/main/java/com/tes/edu/
├── MainActivity.kt                  # 入口 + Compose 底部导航脚手架
├── ui/screens/Screens.kt            # 六大能力页（图谱已落地，其余占位）
├── ui/screens/GraphScreen.kt        # T-B5 知识图谱可视化（Canvas 渲染）
├── ui/graph/GraphLayout.kt          # 章节带布局算法（纯 Kotlin，可单测）
├── viewmodel/SolveViewModel.kt      # 意图分发（文本题/图片题/查点/图谱）
├── viewmodel/GraphViewModel.kt      # 图谱页状态（登录 + 拉图 + 选中态）
├── data/remote/ApiService.kt        # Retrofit 接口（契约对齐 /api/app/*）
├── data/remote/GraphModels.kt       # T-B5 图谱 DTO（nodes/edges/summary）
├── data/remote/AuthModels.kt        # 登录 DTO（占位 /login）
├── data/remote/SessionStore.kt      # 令牌持久化（SharedPreferences）
├── data/remote/AppContextProvider.kt# Application Context 提供者
└── data/local/
    ├── entities/DrillCard.kt        # 背诵卡实体
    ├── entities/WrongQuestion.kt    # 错题本实体
    └── AppDatabase.kt               # Room 数据库
```

## 六大能力页

1. **输入 / 语音**：文本或语音提问
2. **拍照识题**：上传图片解题
3. **讲解展示**：步骤与公式渲染
4. **触类旁通**：相似题推荐列表
5. **知识图谱**（T-B5 已落地）：章节带布局的 DAG 渲染，节点颜色=掌握度
   （绿=已掌握 / 黄=巩固中 / 红=薄弱 / 灰=未学），蓝环=ZPD 待学；
   支持拖动平移、双指缩放、点节点看掌握度/练习统计/前置链详情。
   数据来自 `GET /learning/knowledge-graph`（web-backend，经网关 jwt_c 路由）。
6. **背诵卡 / 错题本**：本地记忆与复习

## 网络

- 默认网关地址（模拟器访问宿主机）：`http://10.0.2.2:8080`
- 接口契约（`ApiService`）：
  - `POST /api/app/solve` — 文本/语音解题与讲解；支持：
    - T-C1 意图三档路由（不传 `mode` 由后端分流 direct/explain/guide）；
    - T-C2/C3 Hint 阶梯对话（`hint=true` + `student_reply`，响应回传
      `hint_level`/`hint_level_name`/`hint_step`/`hint_step_total` 等）；
    - T-C3 "直接看解析"一等功能（`mode=direct` 直达 L4 完整解析 + 复盘三问；
      点击行为由后端入 `direct_reveal` 埋点，不作为违规）；
    - T-C3 题型路由回传（`question_tier`=memory/medium/finale、
      `hint_max_rounds`=该题引导轮次上限）。
  - `POST /api/app/solve_image` — 拍照识题（multipart 图片）
  - `POST /api/app/similar` — 触类旁通推荐
  - `POST /login` — 占位登录换 JWT（web-backend；联调用 student@tes.edu / demo1234）
  - `GET /learning/knowledge-graph?student_id=…` — T-B5 图谱快照（需 Bearer）

## 单测

- `app/src/test/java/com/tes/edu/viewmodel/SolveViewModelTest.kt`：JVM 单测
  （fake ApiService），覆盖 T-C3 "直接看解析"链路与 Hint 阶梯状态机的 mock 走通。
  Android Studio / CI 执行 `./gradlew test`。

## 本地存储

- Room 缓存**背诵卡**（`drill_cards`）与**错题本**（`wrong_questions`），无网可背可复习。

## 构建运行

用 **Android Studio** 打开 `student-app/`，Sync Project 后运行到模拟器或真机。
网关地址如需修改，调整 `ApiService` 中的 `BASE_URL` 即可。
