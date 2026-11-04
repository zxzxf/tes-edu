package com.tes.edu.data.local

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.tes.edu.data.local.entities.ExamListCache
import com.tes.edu.data.remote.AppContextProvider
import com.tes.edu.data.remote.StudentExamItem
import com.tes.edu.data.remote.StudentSession

/**
 * 作业列表缓存（弱网/离线进来能看见上次的作业清单）。
 *
 * 和 [ExamPaperCacheStore] 一样抽成接口：ViewModel 的单测里换内存实现，
 * 「网络失败回退缓存」这条逻辑才不用上真机验证。
 *
 * 键是**当前学生 id**，换账号不会串到别人的清单。
 */
interface ExamListCacheStore {
    /** 命中返回清单，未命中（含反序列化失败）返回 null。 */
    suspend fun load(): List<StudentExamItem>?

    suspend fun save(items: List<StudentExamItem>)
}

/** Room 实现。DAO/学生 id 都延迟到首次使用才取，JVM 单测构造即崩的问题不存在。 */
class RoomExamListCacheStore : ExamListCacheStore {

    private val gson = Gson()
    private val type = object : TypeToken<List<StudentExamItem>>() {}.type

    private val dao by lazy { AppDatabase.get(AppContextProvider.context).examListCacheDao() }

    /** 未登录/取不到会话时用匿名键：宁可不复用，也不跨账号串清单。 */
    private fun key(): String =
        runCatching { StudentSession.currentId()?.takeIf { it.isNotBlank() } }
            .getOrNull() ?: ANONYMOUS

    override suspend fun load(): List<StudentExamItem>? {
        val row = dao.get(key()) ?: return null
        return runCatching {
            gson.fromJson<List<StudentExamItem>>(row.itemsJson, type)
        }.getOrNull()
    }

    override suspend fun save(items: List<StudentExamItem>) {
        dao.upsert(
            ExamListCache(
                cacheKey = key(),
                itemsJson = gson.toJson(items),
                updatedAt = System.currentTimeMillis()
            )
        )
    }

    private companion object {
        const val ANONYMOUS = "anonymous"
    }
}
