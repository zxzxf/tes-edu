package com.tes.edu.data.remote

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import com.google.gson.annotations.SerializedName
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming
import java.io.File

/** 学生端「已有讲解视频」列表项（不返回 video_url，取流一律按 id 走 stream）。 */
data class ExplanationVideoItem(
    val id: String = "",
    @SerializedName("question_text") val questionText: String? = null,
    @SerializedName("knowledge_point") val knowledgePoint: String? = null,
    @SerializedName("duration_sec") val durationSec: Double? = null,
    @SerializedName("created_at") val createdAt: String? = null
)

data class ExplanationVideoListResp(
    val total: Int = 0,
    val items: List<ExplanationVideoItem> = emptyList()
)

/** 错题本按题自助生成：讲稿用「题干 + 参考答案 + 我的作答」拼好，服务端只做幂等排队。 */
data class EnsureExplanationVideoReq(
    val question: String,
    val script: String? = null,
    @SerializedName("knowledge_point") val knowledgePoint: String? = null
)

/**
 * #28 学生端「看视频讲解」取流接口。
 *
 * 独立成接口（与 [ApiService] 分开）：取流返回二进制，且现有 ViewModel 单测里的
 * FakeApi 不必跟着补方法。共用 [ApiClient] 的 Retrofit / OkHttp 连接池，
 * 因此 [AuthInterceptor] 会自动带上 Bearer——这是网关 `jwt_c` 放行的前提。
 */
interface ExplanationVideoStreamApi {

    /** @Streaming：不把整段 mp4 读进内存，直接流式写文件。 */
    @Streaming
    @GET("/api/student/explanation-video/{video_id}/stream")
    suspend fun stream(@Path("video_id") videoId: String): ResponseBody

    /**
     * 本租户已出片视频列表。之前只有 by-question 逐字匹配，教师端生成的视频
     * 学生完全看不到，App 里就表现为「看视频讲解不能用」。
     */
    @GET("/api/student/explanation-video/list")
    suspend fun list(@Query("limit") limit: Int): ExplanationVideoListResp

    /** 按这道题的题干幂等触发 / 复用一条专属讲解视频。 */
    @POST("/api/student/explanation-video/ensure")
    suspend fun ensure(@Body req: EnsureExplanationVideoReq): ExplanationVideoResp
}

/** 拉取可播视频目录；同样走 [LoginManager.withAuth] 保证 Bearer / 过期自动重登。 */
suspend fun listExplanationVideos(limit: Int = 50): List<ExplanationVideoItem> =
    LoginManager.withAuth { ApiClient.videoStream.list(limit) }.getOrThrow().items

/**
 * 错题本点「视频讲解」的完整链路：ensure 排队 → 轮询 by-question 到 done → 返回可播 id。
 *
 * IVH 出片是分钟级，不能让按钮卡死不反馈；[onProgress] 把等待进度回吐给卡片。
 * 只认 ensure 返回的那个任务 id：`by-question` 有近似题干兜底匹配，绝不能把
 * 别道题的视频当成这道题的讲解播出去。
 */
suspend fun ensurePlayableVideoId(
    question: String,
    script: String,
    knowledgePoint: String? = null,
    onProgress: (String) -> Unit = {}
): String {
    var resp = LoginManager.withAuth {
        ApiClient.videoStream.ensure(
            EnsureExplanationVideoReq(question, script, knowledgePoint)
        )
    }.getOrThrow()
    val taskId = resp.id ?: error("视频任务缺少 id")
    var waited = 0L
    val step = 5_000L
    val limit = 180_000L
    while (resp.status != "done") {
        if (resp.status == "failed") error("视频生成失败：${resp.error ?: "请稍后再试"}")
        if (resp.status == "none") error("讲解视频任务丢失，请重试")
        if (waited >= limit) error("视频还在生成中，请稍后再点一次这道题")
        onProgress("这道题的讲解视频生成中…（已等待 ${waited / 1000}s）")
        delay(step)
        waited += step
        resp = LoginManager.withAuth {
            ApiClient.instance.explanationVideo(question)
        }.getOrThrow()
        if (resp.status == "done" && resp.id != taskId) {
            error("讲解视频任务状态不一致，请重试")
        }
    }
    return taskId
}

private val UNSAFE_FILENAME = Regex("[^A-Za-z0-9_-]")

/**
 * 把带鉴权的视频流下载到应用私有缓存目录，返回可供 VideoView 播放的本地文件。
 *
 * 为什么不直接 `setVideoURI(videoUrl)`：
 * 1. 后端 `video_url` 是**相对路径** `/api/student/explanation-videos/{id}.mp4`，
 *    MediaPlayer 无 scheme/host 根本无法加载；
 * 2. 即便拼上 host，`VideoView`/`MediaPlayer` 也**发不出 Authorization 头**，
 *    网关 `/api/student/` 是 `jwt_c`，裸请求必 401。
 *
 * 走 Retrofit（带 Bearer）取流 → 落地到 cacheDir → `setVideoPath(绝对路径)`，
 * 一次性解决 host、鉴权、租户复校（服务端按 id 校验）三件事。
 */
suspend fun downloadExplanationVideo(context: Context, videoId: String): File {
    val body: ResponseBody = LoginManager.withAuth {
        ApiClient.videoStream.stream(videoId)
    }.getOrThrow()

    val safeId = videoId.replace(UNSAFE_FILENAME, "_")
    val dir = File(context.applicationContext.cacheDir, "explanation_videos")
        .apply { mkdirs() }
    val target = File(dir, "$safeId.mp4")
    val part = File(dir, "$safeId.mp4.part")

    return withContext(Dispatchers.IO) {
        try {
            body.use { b ->
                b.byteStream().use { input ->
                    part.outputStream().use { out -> input.copyTo(out) }
                }
            }
            if (part.length() <= 0L) {
                part.delete()
                error("视频流为空")
            }
            // 每次重新下载：教师端 regen 后 id 不变但内容会变，不能用旧缓存顶掉新片。
            if (target.exists()) target.delete()
            if (part.renameTo(target)) target else part
        } catch (e: Exception) {
            part.delete()
            throw e
        }
    }
}
