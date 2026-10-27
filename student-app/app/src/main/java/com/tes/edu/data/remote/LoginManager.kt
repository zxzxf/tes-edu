package com.tes.edu.data.remote

/**
 * 统一登录入口（占位账号换 JWT）。
 *
 * 坑（2026-10-27 修复）：此前只有图谱页（GraphViewModel.ensureLogin）会登录，
 * 输入/触类/拍照三个模块直接打 /api/app/*——网关 /api/app/ 是 jwt_c 模式，
 * 冷启动没有 token 就 401 "missing token"（输入模块报 401 的根因）。
 * 所有需要身份的请求都必须经 [withAuth] 先确保登录。
 */
object LoginManager {
    /** 占位演示账号（P2 接真实用户库后替换为登录页输入）。 */
    const val DEV_USERNAME = "student@tes.edu"
    const val DEV_PASSWORD = "demo1234"

    /**
     * 无 token 时用占位账号换 JWT 并落盘；返回是否已具备登录态。
     *
     * - 已有 token：直接 true；
     * - 无 token：登录成功 true / 失败 false；
     * - 无 Android 环境（JVM 单测，[AppContextProvider] 未 init）：视为已登录
     *   放行——单测注入的 fake ApiService 照常被调用，不因鉴权挂测试。
     */
    suspend fun ensureLogin(): Boolean {
        val session = try {
            SessionStore(AppContextProvider.context)
        } catch (e: Exception) {
            return true // JVM 单测环境：无 Context，放行给注入的 fake
        }
        if (!session.token.isNullOrBlank()) return true
        return try {
            val res = ApiClient.instance.login(LoginRequest(DEV_USERNAME, DEV_PASSWORD))
            session.token = res.accessToken
            session.role = res.role
            ApiClient.rebuild() // 重建 Retrofit，让 AuthInterceptor 带上新 token
            true
        } catch (e: Exception) {
            false
        }
    }

    /** 确保登录后执行请求；登录失败时直接返回失败结果，不发请求（避免必 401）。 */
    suspend fun <T> withAuth(block: suspend () -> T): Result<T> =
        if (ensureLogin()) runCatching { block() }
        else Result.failure(IllegalStateException("登录失败，请检查网络后重试"))
}
