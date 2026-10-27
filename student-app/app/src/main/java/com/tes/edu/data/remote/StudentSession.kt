package com.tes.edu.data.remote

/**
 * 当前学生的会话信息：学生 id + 错题同步游标。
 *
 * 学生 id 怎么来的（读代码结论）：
 *  - `SessionStore` 只存 token / role，登录响应（web-backend `/login`）也只有
 *    access_token 与 role，**不带 student_id**——网关注入的 X-User-Id 是
 *    user_id 不是学生流水号；
 *  - 图谱页既有做法是常量 `DEFAULT_STUDENT_ID = "student_001"`（106 库真实流水）。
 *
 * 所以这里：未登录（无 token）→ null，调用方安全跳过同步；已登录 → 取
 * SessionStore 里落盘的 studentId（P2 登录返回真实 id 后写入即可），
 * 没有则回落到与图谱页一致的演示学生，保证同一账号前后端对得上。
 */
object StudentSession {

    /** 与 GraphUiState.DEFAULT_STUDENT_ID 保持一致：同一个演示学生。 */
    const val DEFAULT_STUDENT_ID = "student_001"

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
        return s.studentId?.takeIf { it.isNotBlank() } ?: DEFAULT_STUDENT_ID
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
