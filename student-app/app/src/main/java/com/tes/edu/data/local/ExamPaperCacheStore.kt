package com.tes.edu.data.local

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.tes.edu.data.local.entities.ExamPaperCache
import com.tes.edu.data.remote.AppContextProvider
import com.tes.edu.data.remote.ExamQuestionView

/**
 * 作业卷题干缓存存储（抽象）。
 *
 * 抽成接口的目的与 [ExamDraftStore] 一样：让 [com.tes.edu.viewmodel.ExamViewModel]
 * 在 JVM 单测里换成内存实现——Room 依赖 Android runtime，直接 new DAO 的话这个状态机
 * 就只能上真机验证，而"缓存命中即秒开 / 网络失败回退缓存"恰恰是最该被锁住的逻辑。
 */
interface ExamPaperCacheStore {
    /** 命中返回题干列表，未命中（含任何异常）返回 null。 */
    suspend fun load(assignmentId: Long): List<ExamQuestionView>?

    suspend fun save(assignmentId: Long, paperId: Long, questions: List<ExamQuestionView>)
}

/** Room 实现。DAO 延迟到首次使用才取，避免在无 Context 的环境（JVM 单测）构造即崩。 */
class RoomExamPaperCacheStore : ExamPaperCacheStore {

    private val gson = Gson()
    private val type = object : TypeToken<List<ExamQuestionView>>() {}.type

    private val dao by lazy { AppDatabase.get(AppContextProvider.context).paperCacheDao() }

    override suspend fun load(assignmentId: Long): List<ExamQuestionView>? {
        val row = dao.get(assignmentId) ?: return null
        return runCatching {
            gson.fromJson<List<ExamQuestionView>>(row.questionsJson, type)
        }.getOrNull()
    }

    override suspend fun save(assignmentId: Long, paperId: Long, questions: List<ExamQuestionView>) {
        dao.upsert(
            ExamPaperCache(
                assignmentId = assignmentId,
                paperId = paperId,
                questionsJson = gson.toJson(questions),
                updatedAt = System.currentTimeMillis()
            )
        )
    }
}
