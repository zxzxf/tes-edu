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

    /**
     * 进卷合并接口：一次拿全题干 + 是否已交 + 判分结果。
     *
     * 弱网下优先走这个；旧版服务端没有该路由会回 404，ViewModel 会回退到
     * [questions] / [result] 两个老接口，保证新旧服务端都能用。
     */
    @GET("api/student/exams/{assignmentId}/bundle")
    suspend fun bundle(@Path("assignmentId") assignmentId: Long): ExamBundleDto

    /** 提交并判分。同一份卷重复提交时服务端返回既有结果（幂等）。 */
    @POST("api/student/exams/{assignmentId}/submit")
    suspend fun submit(
        @Path("assignmentId") assignmentId: Long,
        @Body req: ExamSubmitRequest
    ): ExamResultDto

    /** 取回历史判分结果（已提交才有，未提交 404）。 */
    @GET("api/student/exams/{assignmentId}/result")
    suspend fun result(@Path("assignmentId") assignmentId: Long): ExamResultDto

    /**
     * M3-1/M3-2 解析：**提交前服务端返回 403**（防抄），提交后按需生成并缓存。
     * `seq` 是卷内题号（1 起）。
     */
    @GET("api/student/exams/{assignmentId}/questions/{seq}/solution")
    suspend fun solution(
        @Path("assignmentId") assignmentId: Long,
        @Path("seq") seq: Int
    ): ExamSolutionDto

    /** M3-4 按卷知识点与前后继（提交前后都可见，不含答案/解析）。 */
    @GET("api/student/exams/{assignmentId}/graph")
    suspend fun paperGraph(@Path("assignmentId") assignmentId: Long): PaperGraphDto

    /**
     * M3-6 结果页逐题知识子图：只围绕这一题的考点 + 一层前驱/后继 + 掌握度。
     * 不含答案/解析，提交前后都可见；题号不存在由服务端给 404。
     */
    @GET("api/student/exams/{assignmentId}/questions/{seq}/graph")
    suspend fun questionGraph(
        @Path("assignmentId") assignmentId: Long,
        @Path("seq") seq: Int
    ): QuestionGraphDto
}
