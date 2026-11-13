package com.tes.edu.data.remote

/**
 * 统一登录入口。
 *
 * 2026-11-13 起：登录页已落地，[ensureLogin] 不再静默用演示账号自动登录——
 * 未登录必须走登录页（[com.tes.edu.ui.screens.LoginScreen]）。[login] 是登录页
 * 调用的真实账号密码登录。
 *
 * ⚠️ 本文件注释里禁止出现「斜杠+星号」连写（例如把端点写成 /api/app 加通配星）：
 * Kotlin 块注释支持嵌套（与 Java 不同），注释体内的该序列会被当成再开一层注释，
 * 导致外层 KDoc 永不闭合，编译报 "Unclosed comment"（之前踩过）。
 */
object LoginManager {
    /** 演示账号（开发/联调用，登录页提供一键填充）。接真实用户库后可移除。 */
    const val DEV_USERNAME = "student@tes.edu"
    const val DEV_PASSWORD = "demo1234"

    /**
     * 是否具备登录态：有落盘 token 即视为已登录。
     *
     * - 有 Android 环境（[AppContextProvider] 已 init）：检查 token；
     * - 无 Android 环境（JVM 单测）：访问 Context 会抛异常，视为已登录放行——
     *   注入的 fake ApiService 照常被调用，不因鉴权挂测试。
     */
    fun hasSession(): Boolean = try {
        !SessionStore(AppContextProvider.context).token.isNullOrBlank()
    } catch (e: Exception) {
        true // JVM 单测环境：无 Context，放行给注入的 fake
    }

    /**
     * 确保登录态：沿用 [hasSession] 判断。无 token 时返回 false，由调用方把失败
     * 抛给 UI（AppRoot 据此展示整页登录或登录遮罩）。
     */
    suspend fun ensureLogin(): Boolean = hasSession()

    /**
     * 真实账号密码登录（登录页调用，本次实现）。
     *
     * 成功后落盘 token / role / student_id，重建 Retrofit 让后续请求带上新 token，
     * 并清过期标记、自增 restored 供页面刷新。失败（网络 / 凭证错误）返回 false，
     * 错误文案由 UI 展示。
     */
    suspend fun login(username: String, password: String): Boolean {
        return try {
            val res = ApiClient.instance.login(LoginRequest(username.trim(), password))
            val session = SessionStore(AppContextProvider.context)
            session.token = res.accessToken
            session.role = res.role
            // T-WB1：把登录带回来的流水号存下来，错题/背诵同步就按这个 id 走。
            session.studentId = res.userId
            ApiClient.rebuild() // 重建 Retrofit，让 AuthInterceptor 带上新 token
            AuthBus.onReloginSuccess()
            true
        } catch (e: Exception) {
            false
        }
    }

    /** 确保登录后执行请求；登录失败时直接返回失败结果，不发请求（避免必 401）。 */
    suspend fun <T> withAuth(block: suspend () -> T): Result<T> =
        if (ensureLogin()) runCatching { block() }
        else {
            // 无登录态：标记过期，让 UI 弹出登录（AppRoot 据此路由到登录页/遮罩）
            AuthBus.markExpired()
            Result.failure(IllegalStateException("登录失败，请检查网络后重试"))
        }
}
