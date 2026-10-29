package com.tes.edu.data.local

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.remote.AppContextProvider

/**
 * 作业草稿存储（抽象）。
 *
 * 抽成接口的唯一目的是让 [com.tes.edu.viewmodel.ExamViewModel] 能在 JVM 单测里
 * 换成内存实现——Room 依赖 Android runtime，直接 new DAO 的话这个状态机就只能
 * 上真机验证，而"草稿有没有恢复、client_id 有没有复用"恰恰是最该被锁住的逻辑。
 */
interface ExamDraftStore {
    suspend fun load(assignmentId: Long): ExamDraft?
    suspend fun save(draft: ExamDraft)
    suspend fun delete(assignmentId: Long)
}

/** Room 实现。DAO 延迟到首次使用才取，避免在无 Context 的环境（JVM 单测）构造即崩。 */
class RoomExamDraftStore : ExamDraftStore {

    private val dao by lazy { AppDatabase.get(AppContextProvider.context).examDraftDao() }

    override suspend fun load(assignmentId: Long): ExamDraft? = dao.get(assignmentId)

    override suspend fun save(draft: ExamDraft) {
        dao.upsert(draft)
    }

    override suspend fun delete(assignmentId: Long) {
        dao.delete(assignmentId)
    }
}

/**
 * 作答 map <-> 草稿 JSON 编解码。
 *
 * 单独拆出来是因为 Gson 对 `Map<Int, String>` 的键会序列化成字符串，反序列化必须
 * 带 TypeToken，写错就是静默空 map——草稿看起来"存了"，回来却是空白。
 */
object ExamAnswerCodec {

    private val type = object : TypeToken<Map<Int, String>>() {}.type

    fun encode(answers: Map<Int, String>): String =
        Gson().toJson(answers.filterValues { it.isNotBlank() })

    fun decode(json: String?): Map<Int, String> {
        if (json.isNullOrBlank()) return emptyMap()
        return runCatching {
            Gson().fromJson<Map<Int, String>>(json, type) ?: emptyMap()
        }.getOrDefault(emptyMap()).filterValues { it.isNotBlank() }
    }
}
