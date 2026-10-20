package com.tes.edu.data.remote

import okhttp3.MultipartBody
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part

// 与 Agent 后端 (FastAPI) 的接口契约保持一致
data class SolveRequest(
    val question: String,
    val subject: String? = null,
    val mode: String = "explain" // explain | similar
)

data class SolveResponse(
    val answer: String,
    val steps: List<String> = emptyList(),
    val similar: List<String> = emptyList(),
    val knowledge_points: List<String> = emptyList()
)

interface ApiService {
    @POST("/api/app/solve")
    suspend fun solve(@Body req: SolveRequest): SolveResponse

    @Multipart
    @POST("/api/app/solve_image")
    suspend fun solveImage(@Part file: MultipartBody.Part): SolveResponse
}

object ApiClient {
    // 模拟器访问宿主机网关用 10.0.2.2；真机请改为网关实际地址
    private const val BASE_URL = "http://10.0.2.2:8080/"

    val instance: ApiService by lazy {
        Retrofit.Builder()
            .baseUrl(BASE_URL)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
            .create(ApiService::class.java)
    }
}
