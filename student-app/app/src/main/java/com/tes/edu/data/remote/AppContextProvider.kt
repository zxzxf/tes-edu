package com.tes.edu.data.remote

import android.content.Context

/**
 * 进程级 Application Context 提供者（仅供 SessionStore 等无 UI 组件使用）。
 * 在 Application/Activity 创建时调用 [init] 一次。
 */
object AppContextProvider {
    @Volatile
    lateinit var context: Context
        private set

    fun init(ctx: Context) {
        if (!::context.isInitialized) {
            context = ctx.applicationContext
        }
    }
}
