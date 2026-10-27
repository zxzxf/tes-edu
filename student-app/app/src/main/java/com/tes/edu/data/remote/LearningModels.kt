package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName

/**
 * 学生端 `learning/` 系列端点的数据契约（后端 FastAPI，字段以后端为准）。
 *
 * 后端一律 snake_case，前端一律驼峰：凡是名字对不上的字段必须有
 * `@SerializedName`，缺一个就会静默拿到 null（Gson 不报未知字段错，
 * 排查起来很贵）。
 */

// ---------------------------------------------------------------- 错题本

/** GET/PUSH 回吐的单条错题（服务端视图）。 */
data class WrongRemote(
    val id: Long = 0,
    @SerializedName("client_id") val clientId: String? = null,
    val question: String = "",
    @SerializedName("my_answer") val myAnswer: String? = null,
    @SerializedName("correct_answer") val correctAnswer: String? = null,
    @SerializedName("knowledge_point") val knowledgePoint: String? = null,
    @SerializedName("error_type") val errorType: String? = null,
    val comment: String? = null,
    val correction: String? = null,
    @SerializedName("first_wrong_step") val firstWrongStep: Int? = null,
    @SerializedName("created_at") val createdAt: Long = 0,
    @SerializedName("updated_at") val updatedAt: Long = 0,
    /** 非空 = 服务端已软删，本地要真删。 */
    @SerializedName("deleted_at") val deletedAt: Long? = null
)

/** GET wrong-questions / POST wrong-questions/sync 的响应。 */
data class WrongSyncResponse(
    val items: List<WrongRemote> = emptyList(),
    /** 服务端时间，用作下一次 pull 的 since（客户端时钟不可信）。 */
    @SerializedName("server_time") val serverTime: Long = 0
)

/** 单条待上传错题。clientId 是服务端的 upsert 键，必须非空。 */
data class WrongPushItem(
    @SerializedName("client_id") val clientId: String,
    val question: String,
    @SerializedName("my_answer") val myAnswer: String? = null,
    @SerializedName("correct_answer") val correctAnswer: String? = null,
    @SerializedName("knowledge_point") val knowledgePoint: String? = null,
    @SerializedName("error_type") val errorType: String? = null,
    val comment: String? = null,
    val correction: String? = null,
    @SerializedName("first_wrong_step") val firstWrongStep: Int? = null,
    @SerializedName("updated_at") val updatedAt: Long = 0,
    val deleted: Boolean = false
)

data class WrongSyncRequest(
    @SerializedName("student_id") val studentId: String,
    val items: List<WrongPushItem> = emptyList()
)

/** 掌握度单点：kp 的知识点、掌握度 0~1、较上周的变化量。 */
data class MasteryPoint(
    val kp: String = "",
    val mastery: Double = 0.0,
    val delta: Double = 0.0
)

data class WrongStats(
    val total: Int = 0,
    @SerializedName("by_error_type") val byErrorType: Map<String, Int> = emptyMap(),
    @SerializedName("by_knowledge_point") val byKnowledgePoint: Map<String, Int> = emptyMap(),
    val mastery: List<MasteryPoint> = emptyList()
)

data class OkResponse(val ok: Boolean = false)

// ---------------------------------------------------------------- 背诵卡

data class DrillCardRemote(
    val id: Long = 0,
    @SerializedName("client_id") val clientId: String? = null,
    val kp: String? = null,
    val front: String = "",
    val back: String = "",
    @SerializedName("due_at") val dueAt: Long = 0,
    val ease: Double = 2.5,
    @SerializedName("interval_days") val intervalDays: Double = 0.0,
    val reps: Int = 0,
    val lapses: Int = 0,
    @SerializedName("deleted_at") val deletedAt: Long? = null
)

data class DrillCardsResponse(
    val items: List<DrillCardRemote> = emptyList(),
    @SerializedName("due_count") val dueCount: Int = 0,
    val total: Int = 0
)

data class DrillPushItem(
    @SerializedName("client_id") val clientId: String,
    val kp: String? = null,
    val front: String,
    val back: String,
    /** 来源标记（如 "wrong_question"），便于后端区分学生自建与系统生成。 */
    val source: String? = null
)

data class DrillPushRequest(
    @SerializedName("student_id") val studentId: String,
    val items: List<DrillPushItem> = emptyList()
)

/** 复习评分：0=忘了 / 3=模糊 / 4=会了 / 5=太简单。 */
data class DrillReviewRequest(val rating: Int = 0)

data class DrillReviewResult(
    val id: Long = 0,
    @SerializedName("due_at") val dueAt: Long = 0,
    @SerializedName("interval_days") val intervalDays: Double = 0.0,
    val ease: Double = 2.5,
    val reps: Int = 0,
    val lapses: Int = 0
)

data class DrillStats(
    val total: Int = 0,
    val due: Int = 0,
    @SerializedName("new_count") val newCount: Int = 0,
    val retention: Double = 0.0
)
