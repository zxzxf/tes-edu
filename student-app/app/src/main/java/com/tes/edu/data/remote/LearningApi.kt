package com.tes.edu.data.remote

import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * 学生端 `learning/` 端点（错题本云同步 + 背诵卡间隔复习）。
 *
 * 与 [ApiService] 分开成接口：这批端点是学生端这一波新增的，
 * 独立接口后既有模块（解题/触类/拍照）的单测 fake 不必跟着改一遍。
 * 路径写法与既有 `learning/knowledge-graph` 一致——不带前导斜杠。
 */
interface LearningApi {

    /**
     * 拉取增量错题。`since`=上次同步的服务端时间（首次 0 拉全量）。
     * `kp` / `error_type` 是当前筛选条件：后端认就服务端过滤，不认也不影响
     * （客户端还会按同一条件再过滤一遍）。
     */
    @GET("learning/wrong-questions")
    suspend fun wrongQuestions(
        @Query("student_id") studentId: String,
        @Query("since") since: Long = 0,
        @Query("kp") kp: String? = null,
        @Query("error_type") errorType: String? = null
    ): WrongSyncResponse

    /** 上传本地 dirty 记录（含软删），服务端 upsert 后回吐完整列表。 */
    @POST("learning/wrong-questions/sync")
    suspend fun syncWrongQuestions(@Body req: WrongSyncRequest): WrongSyncResponse

    /** 硬删一条服务端记录（本波流程走软删，端点按契约保留备用）。 */
    @DELETE("learning/wrong-questions/{id}")
    suspend fun deleteWrongQuestion(
        @Path("id") id: Long,
        @Query("student_id") studentId: String
    ): OkResponse

    /** 掌握度 / 错因分布统计。拿不到就隐藏统计区，不算失败。 */
    @GET("learning/wrong-questions/stats")
    suspend fun wrongStats(@Query("student_id") studentId: String): WrongStats

    @GET("learning/drill-cards")
    suspend fun drillCards(
        @Query("student_id") studentId: String,
        @Query("due_only") dueOnly: Int = 1,
        @Query("limit") limit: Int = 20
    ): DrillCardsResponse

    @POST("learning/drill-cards")
    suspend fun pushDrillCards(@Body req: DrillPushRequest): DrillCardsResponse

    /** 提交一次复习评分，服务端重算排程并回吐权威值。 */
    @POST("learning/drill-cards/{id}/review")
    suspend fun reviewDrillCard(
        @Path("id") id: Long,
        @Query("student_id") studentId: String,
        @Body req: DrillReviewRequest
    ): DrillReviewResult

    @GET("learning/drill-cards/stats")
    suspend fun drillStats(@Query("student_id") studentId: String): DrillStats
}
