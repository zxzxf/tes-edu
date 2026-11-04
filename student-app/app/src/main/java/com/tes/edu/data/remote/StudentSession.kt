package com.tes.edu.data.remote

/**
 * 当前学生的会话信息：学生 id + 错题同步游标。
 *
 * 学生 id 怎么来的（T-WB1，2026-10-28 已接上）：
 *  - 登录响应 `/login` 现在带 `user_id`，`LoginManager.ensureLogin` 把它写进
 *    `SessionStore.studentId`——服务端对 student 角色强制用网关注入的 X-User-Id
 *    作 student_id，所以 user_id 就是同步该用的流水号，两端口径天然一致；
 *  - 老安装只有 token、没存流水号时，`ensureLogin` 会补一次登录把它换回来。
 *
 * 所以这里：未登录（无 token）→ null，调用方安全跳过同步；已登录 → 取落盘的
 * studentId；仍拿不到（老服务端不返回该字段）才回落到登录账号本身。
 *
 * 口径红线（2026-11-04）：**绝不回落到演示流水号 student_001**。错题/背诵接口
 * 后端会按 JWT 强制覆盖 student_id，回落值传了也不生效；但图谱接口客户端传什么
 * 就查谁，一回落就会去查另一个学生的空学情，出现「作业有数据、图谱全空」。
 * 登录换来的 JWT uid 就是登录账号名，回落用它才与其余页面一致。
 */
object StudentSession {

    private fun store(): SessionStore? = try {
        SessionStore(AppContextProvider.context)
    } catch (e: Exception) {
        // JVM 单测 / Application 未初始化：没有 Context 就没有登录态
        null
    }

    /** 当前学生 id；未登录返回 null（同步应当跳过而不是崩）。 */
    fun currentId(): String? {
        val s = store() ?: return null
        if (s.token.isNullOrBlank()) return null
        return s.studentId?.takeIf { it.isNotBlank() } ?: LoginManager.DEV_USERNAME
    }

    /** 下次 pull 的 `since`：0 = 全量。 */
    fun wrongSince(): Long = store()?.wrongSyncAt ?: 0

    /**
     * 记下本次同步的服务端时间。服务端没给（0）就用本地时间兜底——
     * 兜底到 0 会让下一次继续全量拉，白费流量。
     */
    fun markWrongSync(serverTime: Long) {
        store()?.wrongSyncAt = if (serverTime > 0) serverTime else System.currentTimeMillis()
    }
}
