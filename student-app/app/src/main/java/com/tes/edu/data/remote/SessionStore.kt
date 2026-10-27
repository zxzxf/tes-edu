package com.tes.edu.data.remote

import android.content.Context
import android.content.SharedPreferences

/**
 * 登录态持久化（P0：SharedPreferences；P2 换 EncryptedSharedPreferences）。
 *
 * 网关注入的 X-User-* 身份来自 JWT，客户端只需保管 token 本体。
 */
class SessionStore(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences("tes_session", Context.MODE_PRIVATE)

    var token: String?
        get() = prefs.getString(KEY_TOKEN, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_TOKEN) else putString(KEY_TOKEN, value)
            }.apply()
        }

    var role: String?
        get() = prefs.getString(KEY_ROLE, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_ROLE) else putString(KEY_ROLE, value)
            }.apply()
        }

    /**
     * 学生流水号（错题/背诵同步的 student_id）。
     *
     * 登录响应只有 token 与 role、不带 student_id，所以这里留一个落盘位：
     * 现在回落到演示学生，P2 登录接口返回真实 id 时写入即可，调用方不用改。
     */
    var studentId: String?
        get() = prefs.getString(KEY_STUDENT_ID, null)
        set(value) {
            prefs.edit().apply {
                if (value.isNullOrBlank()) remove(KEY_STUDENT_ID) else putString(KEY_STUDENT_ID, value)
            }.apply()
        }

    /**
     * 上次错题同步的服务端时间戳（下次 pull 的 `since`）。
     * 存服务端时间而不是本地时间：客户端时钟可能偏，偏差会把增量拉漏。
     */
    var wrongSyncAt: Long
        get() = prefs.getLong(KEY_WRONG_SYNC_AT, 0)
        set(value) {
            prefs.edit().putLong(KEY_WRONG_SYNC_AT, value).apply()
        }

    fun clear() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val KEY_TOKEN = "access_token"
        private const val KEY_ROLE = "role"
        private const val KEY_STUDENT_ID = "student_id"
        private const val KEY_WRONG_SYNC_AT = "wrong_sync_at"
    }
}
