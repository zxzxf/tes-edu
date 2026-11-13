package com.tes.edu.data.remote

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 登录态事件总线：在 Compose 之外也能发射事件（例如 OkHttp 拦截器在后台线程检测到 401）。
 *
 * - [expired]：登录态失效（token 过期 / 被服务端拒绝）。UI 据此弹出登录遮罩。
 *   token 置空与置 true 两步都在拦截器里完成，避免重入导致弹多次。
 * - [sessionRestored]：重新登录成功后自增，供页面重跑数据拉取（401 后页面已停在错误态，
 *   用户重新登录后需要让当前页重新去后端取数）。
 */
object AuthBus {

    private val _expired = MutableStateFlow(false)
    val expired = _expired.asStateFlow()

    private val _sessionRestored = MutableStateFlow(0L)
    val sessionRestored = _sessionRestored.asStateFlow()

    /** 标记登录态失效（拦截器 / 无登录态时调用）。 */
    fun markExpired() {
        _expired.value = true
    }

    /** 重新登录成功：清过期标记 + 自增 restored，触发页面刷新。 */
    fun onReloginSuccess() {
        _expired.value = false
        _sessionRestored.value += 1
    }
}
