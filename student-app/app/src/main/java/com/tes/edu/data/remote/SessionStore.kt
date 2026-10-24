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

    fun clear() {
        prefs.edit().clear().apply()
    }

    companion object {
        private const val KEY_TOKEN = "access_token"
        private const val KEY_ROLE = "role"
    }
}
