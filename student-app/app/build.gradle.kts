plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    // Room 注解处理器（P0 本地库骨架）：须在本模块应用（apply false 会导致
    // ksp(...) 依赖配置不存在——脚本编译期即报 Unresolved reference）
    id("com.google.devtools.ksp") version "1.9.24-1.0.20"
}

android {
    namespace = "com.tes.edu"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.tes.edu"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// 显式钉死 Kotlin/KSP 的 jvmTarget=17，与 compileOptions 对齐。
// Gradle daemon 跑在 JDK 21 上（中文 Windows 下 JDK17 启动器按 GBK 读
// @argfile 会读花 classpath 里的中文路径，导致测试 worker CNFE——见 README
// 「工具链」一节），KGP 默认 target 会跟随 daemon JDK 变 21，触发
// "Inconsistent JVM-target compatibility" 校验失败。
tasks.withType<org.jetbrains.kotlin.gradle.tasks.KotlinCompile>().configureEach {
    kotlinOptions.jvmTarget = "17"
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    // 底部导航 4 个语义图标（MenuBook/AutoStories/AccountTree/Bookmark）不在
    // material-icons-core 集内；release 构建 R8 会裁掉未引用图标。
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.0")
    implementation("androidx.navigation:navigation-compose:2.7.7")

    // 网络：Retrofit（经网关访问 Agent 后端）
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    // T-B5：认证拦截器直接用 OkHttpClient（Retrofit 传递依赖，显式声明锁定版本）
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // 本地库：Room（背诵卡 / 错题本离线缓存）
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    // JVM 单测：T-C3 "直接看解析"/Hint 阶梯的 App 侧 mock 走通
    // （fake ApiService + ViewModel 状态机；./gradlew test 可跑）
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
}

// T-C3 验证：在本机非 ASCII 工作区路径下，Gradle 的 Test 任务会用 @argfile 把
// classpath 传给 worker，而 Windows 中文环境下 JDK 启动器按 GBK（sun.jnu.encoding）
// 读 @argfile，把路径里的中文（如 “服务器”）读花，导致 ClassNotFoundException。
// JEP 400 只把 file.encoding 改成 UTF-8，sun.jnu.encoding 仍是 GBK，故无法根治。
// 此 task 改用 JavaExec 直接以命令行传 classpath（<32KB，UTF-16 解码正确），
// 可靠跑通 SolveViewModelTest。长期根治：把工作区迁到纯 ASCII 路径后改回标准 test。
tasks.register("runUnitTests", JavaExec::class) {
    group = "verification"
    description = "Run SolveViewModelTest via JUnitCore (bypasses worker @argfile on non-ASCII paths)"
    val testTask = tasks.named<org.gradle.api.tasks.testing.Test>("testDebugUnitTest")
    classpath = testTask.get().classpath
    mainClass.set("org.junit.runner.JUnitCore")
    args("com.tes.edu.viewmodel.SolveViewModelTest")
    dependsOn("compileDebugUnitTestKotlin")
}
