package com.tes.edu.data.remote

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

/**
 * 学生端作业卷端点（网关 `/api/student/exams`）。
 *
 * 与 [LearningApi] 分开成接口的理由相同：新端点的 fake 不必污染既有模块的单测。
 * 路径带 `api/` 前缀（网关按 /api/student/ 转发），且**不带前导斜杠**——
 * 前导斜杠会让 Retrofit 丢掉 baseUrl 里的路径部分。
 */
interface ExamApi {

    /** 可做的卷（服务端按年级段/指定学生下发，且只返回已发布的）。 */
    @GET("api/student/exams")
    suspend fun list(): List<StudentExamItem>

    /** 取卷：题干 + 选项，不含答案。 */
    @GET("api/student/exams/{assignmentId}")
    suspend fun questions(@Path("assignmentId") assignmentId: Long): List<ExamQuestionView>

    /** 提交并判分。同一份卷重复提交时服务端返回既有结果（幂等）。 */
    @POST("api/student/exams/{assignmentId}/submit")
    suspend fun submit(
        @Path("assignmentId") assignmentId: Long,
        @Body req: ExamSubmitRequest
    ): ExamResultDto

    /** 取回历史判分结果（已提交才有，未提交 404）。 */
    @GET("api/student/exams/{assignmentId}/result")
    suspend fun result(@Path("assignmentId") assignmentId: Long): ExamResultDto
}
