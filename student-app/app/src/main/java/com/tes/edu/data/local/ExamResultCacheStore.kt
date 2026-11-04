package com.tes.edu.data.local

import com.google.gson.Gson
import com.tes.edu.data.local.entities.ExamResultCache
import com.tes.edu.data.remote.AppContextProvider
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.StudentSession

/**
 * 判分结果缓存（结果页弱网秒开）。
 *
 * 抽成接口与 [ExamListCacheStore] 同理：JVM 单测换内存实现，
 * 「网络失败回退缓存」这条逻辑不用上真机验证。
 */
interface ExamResultCacheStore {
    /** 命中返回结果，未命中（含反序列化失败）返回 null。 */
    suspend fun load(assignmentId: Long): ExamResultDto?

    suspend fun save(assignmentId: Long, result: ExamResultDto)
}

/** Room 实现。DAO/学生 id 都延迟到首次使用才取，JVM 单测构造即崩的问题不存在。 */
class RoomExamResultCacheStore : ExamResultCacheStore {

    private val gson = Gson()

    private val dao by lazy { AppDatabase.get(AppContextProvider.context).examResultCacheDao() }

    private fun key(assignmentId: Long): String {
        val student = runCatching { StudentSession.currentId()?.takeIf { it.isNotBlank() } }
            .getOrNull() ?: ANONYMOUS
        return "$student:$assignmentId"
    }

    override suspend fun load(assignmentId: Long): ExamResultDto? {
        val row = dao.get(key(assignmentId)) ?: return null
        return runCatching { gson.fromJson(row.resultJson, ExamResultDto::class.java) }.getOrNull()
    }

    override suspend fun save(assignmentId: Long, result: ExamResultDto) {
        dao.upsert(
            ExamResultCache(
                cacheKey = key(assignmentId),
                assignmentId = assignmentId,
                resultJson = gson.toJson(result),
                updatedAt = System.currentTimeMillis()
            )
        )
    }

    private companion object {
        const val ANONYMOUS = "anonymous"
    }
}
