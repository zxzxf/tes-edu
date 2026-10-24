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
    val mode: String = "explain", // explain | similar
    // S4 过程采集：学生作答（解答题须贴过程文本），非空才会触发判分
    @SerializedName("student_answer") val studentAnswer: String? = null
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

data class SolveResponse(
    val answer: String,
    val steps: List<String> = emptyList(),
    val similar: List<String> = emptyList(),
    @SerializedName("knowledge_points") val knowledgePoints: List<String> = emptyList(),
    @SerializedName("content_hash") val contentHash: String? = null,
    val judgement: Judgement? = null
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
    // 模拟器访问宿主机网关用 10.0.2.2；真机请改为网关实际地址
    private const val BASE_URL = "http://10.0.2.2:8080/"

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
