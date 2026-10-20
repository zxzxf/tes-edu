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
├── ui/screens/Screens.kt            # 六大能力页（占位）
├── viewmodel/SolveViewModel.kt      # 意图分发（文本题/图片题/查点/图谱）
├── data/remote/ApiService.kt        # Retrofit 接口（契约对齐 /api/app/*）
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
5. **知识图谱**：知识点关系可视化
6. **背诵卡 / 错题本**：本地记忆与复习

## 网络

- 默认网关地址（模拟器访问宿主机）：`http://10.0.2.2:8080`
- 接口契约（`ApiService`）：
  - `POST /api/app/solve` — 文本/语音解题与讲解
  - `POST /api/app/solve_image` — 拍照识题（multipart 图片）
  - `POST /api/app/similar` — 触类旁通推荐

## 本地存储

- Room 缓存**背诵卡**（`drill_cards`）与**错题本**（`wrong_questions`），无网可背可复习。

## 构建运行

用 **Android Studio** 打开 `student-app/`，Sync Project 后运行到模拟器或真机。
网关地址如需修改，调整 `ApiService` 中的 `BASE_URL` 即可。
