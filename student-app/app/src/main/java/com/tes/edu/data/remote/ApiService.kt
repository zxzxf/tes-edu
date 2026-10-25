package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName
import okhttp3.Interceptor
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Query

// 与 Agent 后端 (FastAPI) 的接口契约保持一致
data class SolveRequest(
    val question: String,
    val subject: String? = null,
    // T-C1：不传（null）由后端按学生意图自动三档路由；显式传值尊重调用方
    // （explain | guide | quick | similar）。
    // T-C3：mode="direct" 是一等功能"直接看解析"的服务端入口——直达 L4
    // 完整解析 + 复盘三问；点击行为入埋点且不作为违规（R6）。
    val mode: String? = null,
    // S4 过程采集：学生作答（解答题须贴过程文本），非空才会触发判分
    @SerializedName("student_answer") val studentAnswer: String? = null,
    // T-C2/C3：Hint 阶梯对话。hint=true 按 (学生, 题) 维护 L0–L4 阶梯，
    // 每轮返回当前等级 Hint；studentReply 是本轮学生的回应
    // （挫败信号检测 + LLM 对话历史；压轴题的有实质回应会推进到下一步）
    val hint: Boolean = false,
    @SerializedName("student_reply") val studentReply: String? = null,
    // T-C2/C3：学生主动点"直接看解析" -> 直达 L4（与 mode="direct" 等价入口）
    @SerializedName("direct_reveal") val directReveal: Boolean = false
)

// judge v2 步骤级归因结果（字段名与后端 snake_case 对齐）
data class Judgement(
    val correct: Boolean? = null,                          // 未判分/需补过程时为 null
    val partial: Boolean? = null,
    val comment: String? = null,
    @SerializedName("first_wrong_step") val firstWrongStep: Int? = null,
    @SerializedName("error_type") val errorType: String? = null,
    @SerializedName("attribution_source") val attributionSource: String? = null,
    val evidence: String? = null,
    val correction: String? = null,
    // S4 提交协议：true = 需补过程，此时 correct 为 null，不算错
    @SerializedName("need_process") val needProcess: Boolean = false
)

/**
 * 触类旁通请求（agent-backend `SimilarRequest`）。
 * @param count 变式题条数，后端默认 3；LLM 生成，实际条数以后端返回为准。
 */
data class SimilarRequest(
    val question: String,
    val subject: String? = null,
    val count: Int = 3
)

/**
 * T-A4 变式题 /learning/variants 的单题。
 *
 * 只声明 App 真正要用的标量字段——`options` 与 `knowledge_points` 在库里是 jsonb，
 * 不同行可能是数组也可能是字符串，声明成 List 会让 Gson 在遇到字符串时整条解析失败。
 * Gson 默认忽略未声明字段，不声明即安全。
 */
data class VariantItem(
    val id: Long? = null,
    val subject: String? = null,
    val type: String? = null,
    val stem: String? = null,
    val answer: String? = null,
    val difficulty: Int? = null,
    @SerializedName("content_hash") val contentHash: String? = null,
    /** 命中的知识点（可能是自身或下游） */
    @SerializedName("kp_hit") val kpHit: String? = null,
    /** 0=同点变式，1=直接下游，2=间接下游 */
    val tier: Int? = null
)

/** T-A4 变式题响应：items 为该知识点（及下游）的真实题目，不足 n 时 note 说明原因。 */
data class VariantsResponse(
    val kp: String? = null,
    val items: List<VariantItem> = emptyList(),
    val total: Int = 0,
    val note: String? = null
)

data class SolveResponse(
    val answer: String,
    val steps: List<String> = emptyList(),
    val similar: List<String> = emptyList(),
    @SerializedName("knowledge_points") val knowledgePoints: List<String> = emptyList(),
    @SerializedName("content_hash") val contentHash: String? = null,
    val judgement: Judgement? = null,
    /**
     * 变式题是否由检索兜底生成（后端 `similar_degraded`）。
     * true 时 `similar` 里是题库片段（截断文本），不是可练的完整题目——
     * 触类页据此禁用「再练这道」，避免把残句当题干搬回输入页。
     */
    @SerializedName("similar_degraded") val similarDegraded: Boolean = false,
    // T-C1 意图三档路由回传（埋点与"当前为引导档/直给档"展示）
    val intent: String? = null,
    val mode: String? = null,
    @SerializedName("intent_source") val intentSource: String? = null,
    // T-C3 题型路由：memory/medium/finale + 轮次上限（决定 UI 策略）
    @SerializedName("question_tier") val questionTier: String? = null,
    @SerializedName("question_tier_name") val questionTierName: String? = null,
    @SerializedName("question_type") val questionType: String? = null,
    @SerializedName("hint_max_rounds") val hintMaxRounds: Int? = null,
    // T-C2/C3 Hint 阶梯回传（仅 hint 轮有值）
    @SerializedName("hint_level") val hintLevel: Int? = null,
    @SerializedName("hint_level_name") val hintLevelName: String? = null,
    @SerializedName("hint_frustration_streak") val hintFrustrationStreak: Int? = null,
    @SerializedName("hint_finale") val hintFinale: Boolean? = null,
    @SerializedName("hint_direct_reveal") val hintDirectReveal: Boolean? = null,
    @SerializedName("hint_rounds") val hintRounds: Int? = null,
    // T-C3 分步拆解回传（仅压轴题 hint 轮有值）
    @SerializedName("hint_step") val hintStep: Int? = null,
    @SerializedName("hint_step_total") val hintStepTotal: Int? = null,
    @SerializedName("hint_step_text") val hintStepText: String? = null
)

interface ApiService {
    @POST("/api/app/solve")
    suspend fun solve(@Body req: SolveRequest): SolveResponse

    @Multipart
    @POST("/api/app/solve_image")
    suspend fun solveImage(@Part file: MultipartBody.Part): SolveResponse

    /** 占位登录（web-backend）：换回 JWT，后续请求带 Bearer。 */
    @POST("login")
    suspend fun login(@Body req: LoginRequest): TokenResponse

    /** T-B5 知识图谱快照：DAG + 掌握度叠层 + ZPD 高亮。 */
    @GET("learning/knowledge-graph")
    suspend fun knowledgeGraph(
        @Query("student_id") studentId: String,
        @Query("subject") subject: String? = null
    ): GraphResponse

    /**
     * 触类旁通：基于题干让 LLM 生成同型/变式题（返回体复用 SolveResponse，
     * 取 `similar` 与 `knowledge_points`，`answer` 在该模式下无意义）。
     */
    @POST("/api/app/similar")
    suspend fun similar(@Body req: SimilarRequest): SolveResponse

    /**
     * T-A4 变式题：给定知识点 code 拉真实题目（同点优先、下游兜底）。
     * 图谱「练这个知识点」走这条——比 LLM 生成更可控，且题在库里可回查。
     */
    @GET("learning/variants")
    suspend fun variants(
        @Query("kp") kp: String,
        @Query("n") n: Int = 1
    ): VariantsResponse
}

/**
 * 认证拦截器：有 token 就带 Bearer。
 * 登录接口本身（/login）不带——网关该路由 mode 为空不鉴权。
 */
class AuthInterceptor(private val session: SessionStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = session.token
        val req = chain.request()
        if (token.isNullOrBlank() || req.url.encodedPath.endsWith("/login")) {
            return chain.proceed(req)
        }
        return chain.proceed(
            req.newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        )
    }
}

object ApiClient {
    // 生产环境：走 Cloudflare 隧道 + HTTPS 公网入口（真机可用）。
    // 模拟器联调本地网关时改回 "http://10.0.2.2:8080/"。
    private const val BASE_URL = "https://sd12sd.icu/"

    @Volatile
    private var cached: ApiService? = null

    /** 登录后调用一次，让后续请求带上新 token（进程内重建 Retrofit 实例）。 */
    fun rebuild() {
        synchronized(this) { cached = null }
    }

    val instance: ApiService get() = cached ?: synchronized(this) {
        cached ?: build().also { cached = it }
    }

    private fun build(): ApiService {
        // SessionStore 需要 Context；用 applicationContext 兜底，避免持 Activity
        val ctx = AppContextProvider.context
        val session = SessionStore(ctx)
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(session))
            .build()
        return Retrofit.Builder()
            .baseUrl(BASE_URL)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}
