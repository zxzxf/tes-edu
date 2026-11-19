package com.tes.edu.state

import com.tes.edu.data.remote.ApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 推送中心学生端状态（未读角标 + 前台长轮询循环）。
 *
 * 轮询口径（计划 v1.5）：服务端挂起上限 25s（< CF 30s 红线），本循环在
 * AppRoot 前台组合期间常驻——poll 返回立即续拉；网络/鉴权异常退避 10s
 * 再试（401 由 AuthBus 统一处理，这里只保证循环不死）。
 * 后台不轮询：消息持久在 PG，下次打开全量补收（"离线不丢"的设计语义）。
 */
object PushStore {

    private val _unreadTotal = MutableStateFlow(0)
    val unreadTotal: StateFlow<Int> = _unreadTotal.asStateFlow()

    /** 有新消息时自增：消息页据此重拉列表。 */
    private val _newMsgTick = MutableStateFlow(0)
    val newMsgTick: StateFlow<Int> = _newMsgTick.asStateFlow()

    private var loopJob: Job? = null

    /** 微秒游标：初值 0=首拉全量新消息；进程内单调推进。 */
    @Volatile
    private var sinceUs: Long = 0

    /** AppRoot 进入时调用（幂等：重复调用只保留一个循环）。 */
    fun start(scope: CoroutineScope) {
        if (loopJob?.isActive == true) return
        loopJob = scope.launch(Dispatchers.IO) {
            // 先校一次全量未读（冷启动角标不从 0 起步），再进长轮询循环
            runCatching { refreshUnread() }
            while (isActive) {
                try {
                    val resp = ApiClient.push.poll(sinceUs)
                    if (resp.items.isNotEmpty()) {
                        sinceUs = maxOf(sinceUs, resp.maxCreatedUs)
                        // 角标以服务端为权威重校准（不本地累加，防漂移）；
                        // 列表重拉交给消息页对 newMsgTick 的订阅，这里不再
                        // 回头调 messages（避免嵌套请求拖慢轮询节奏）
                        runCatching { _unreadTotal.value = ApiClient.push.messages(size = 1).unreadTotal }
                        _newMsgTick.value += 1
                    } else {
                        sinceUs = resp.maxCreatedUs
                    }
                } catch (_: Exception) {
                    // 网络抖动/网关超时：退避后重试，循环绝不退出
                    delay(10_000)
                }
            }
        }
    }

    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    /** 重新从服务端校准未读总数（消息页进/出、标已读后调用）。 */
    suspend fun refreshUnread() {
        val data = ApiClient.push.messages(size = 1)
        _unreadTotal.value = data.unreadTotal
    }

    /** 标已读并同步角标（服务端是权威，本地只即时扣 1 优化观感）。 */
    suspend fun markRead(msgId: String) {
        ApiClient.push.markRead(msgId)
        if (_unreadTotal.value > 0) _unreadTotal.value -= 1
    }

    suspend fun markAllRead(topicCode: String? = null) {
        ApiClient.push.readAll(topicCode)
        _unreadTotal.value = 0
    }

    /** 仅单测用。 */
    fun resetForTest() {
        stop()
        _unreadTotal.value = 0
        _newMsgTick.value = 0
        sinceUs = 0
    }
}
