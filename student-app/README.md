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
├── MainActivity.kt                  # 入口 + Compose 底部导航脚手架（页内联动跳转）
├── ui/screens/Screens.kt            # 六大能力页（输入/图谱/触类已落地，其余占位）
├── ui/screens/GraphScreen.kt        # T-B5 知识图谱可视化（Canvas 渲染 + 节点联动）
├── ui/graph/GraphLayout.kt          # 章节带布局算法（纯 Kotlin，可单测）
├── viewmodel/SolveViewModel.kt      # 意图分发（文本题/图片题/查点/图谱）
├── viewmodel/SimilarViewModel.kt    # 触类旁通（三态 + 降级标记）
├── viewmodel/GraphViewModel.kt      # 图谱页状态（登录 + 拉图 + 选中态 + 按知识点拉题）
├── state/AppBridge.kt               # 跨 tab 一次性传参（进程内 StateFlow 单例）
├── data/remote/ApiService.kt        # Retrofit 接口（/api/app/* + /learning/*）
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
4. **触类旁通**（波 1 已落地）：`POST /api/app/similar` 生成同型/变式题，
   点「再练这道」把题干带回输入页；来源题干默认取最近做的一题，可改可「换一批」。
   ⚠️ 后端 LLM 额度受限时会走检索兜底（`similar_degraded=true`），
   此时列表是**题库片段而非完整题目**，页面会禁用「再练这道」并提示。
5. **知识图谱**（T-B5 已落地，波 1 补联动）：章节带布局的 DAG 渲染，节点颜色=掌握度
   （绿=已掌握 / 黄=巩固中 / 红=薄弱 / 灰=未学），蓝环=ZPD 待学；
   支持拖动平移、双指缩放、点节点看掌握度/练习统计/前置链详情。
   数据来自 `GET /learning/knowledge-graph`（web-backend，经网关 jwt_c 路由）。
   节点详情两个出口：**「练这个知识点」** 走 `GET /learning/variants?kp=<code>&n=1`
   取**题库真题**的题干跳输入页（不依赖 LLM 额度）；**「背这个知识点」** 把
   knowledge_point 交给背诵页限定范围（背诵页本体属波 3）。
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

三个 JVM 单测类，**合计 20 tests / 0 failures / 0 errors / 0 skipped**（2026-10-27，JDK 17 下跑通）：

| 测试类 | 用例数 | 覆盖 |
|---|---|---|
| `viewmodel/SolveViewModelTest.kt` | 4 | T-C3 "直接看解析"链路与 Hint 阶梯状态机（fake ApiService） |
| `viewmodel/SimilarViewModelTest.kt` | 11 | 触类旁通：三态、空白题干不发请求、降级标记透传、后端 DTO 解析（含 jsonb 字段忽略） |
| `state/AppBridgeTest.kt` | 5 | 跨 tab 传参的一次性消费语义（投递→取出→清空） |

- 执行（**必须显式指定 JDK 17**，见下节工具链）：

  ```bash
  cd student-app
  JAVA_HOME=<repo>/../tools/jdk-17.0.20.1+1 ./gradlew :app:testDebugUnitTest --no-daemon --console=plain
  ```

- 报告：`app/build/test-results/testDebugUnitTest/*.xml`（每个测试类一个）。
- ⚠️ 写 Kotlin 单测时注意：`"..."` 里的 `$` 是模板起始符，题干含 `$U=` 这类 LaTeX
  必须写成 `\$U=`，否则编译报 `Unresolved reference`。

## 本地存储

- Room 缓存**背诵卡**（`drill_cards`）与**错题本**（`wrong_questions`），无网可背可复习。

## 本地工具链（离线自备，不依赖系统环境）

构建需要 **JDK 17** 与 **Android SDK**；两者已放在仓库外的 `tools/` 下（不入库），
`local.properties` 已指向 SDK：

- **JDK 17**：`tools/jdk-17.0.20.1+1`（另有 `tools/jdk-21.0.12.1+1`、`tools/jdk17`）
  —— ⚠️ 本机 `java -version` 默认是 **1.8.0_291**，Gradle 8.7 + AGP 会直接失败，
  **命令行构建必须显式设 `JAVA_HOME`**（见下单测/构建命令示例）。
- **Android SDK**：`tools/android-sdk`（`local.properties` 的 `sdk.dir` 已配置）。
- Gradle Wrapper 已入库（`gradlew` / `gradlew.bat`），无需全局 Gradle。

## 构建运行

用 **Android Studio** 打开 `student-app/`，Sync Project 后运行到模拟器或真机
（Studio 里把 Gradle JDK 指到上述 JDK 17）。命令行打包 debug APK：

```bash
cd student-app
JAVA_HOME=<repo>/../tools/jdk-17.0.20.1+1 ./gradlew :app:assembleDebug --no-daemon --console=plain
```

网关地址如需修改，调整 `ApiService` 中的 `BASE_URL` 即可（模拟器默认 `http://10.0.2.2:8080`）。
