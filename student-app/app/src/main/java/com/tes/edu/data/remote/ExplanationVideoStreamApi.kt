package com.tes.edu.data.remote

import android.content.Context
import kotlinx.coroutines.Dispatchers
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
 * 查这道题有没有已出片视频：一次轻量 GET，立刻返回。
 *
 * 真机实测 IVH 出片要 1–9 分钟，早期版本在这里做 60 秒的循环轮询，
 * 按钮一直「准备中…」、移动网络一抖动就抛 readTimeout，看起来就是「生成超时」。
 * 现在改成**一次查询 + 立即反馈**：done 直接播，processing 只显示状态，
 * 需要生成时另调一次 ensure 排队后马上把按钮还给用户。
 */
suspend fun lookupWrongVideo(question: String): ExplanationVideoResp =
    LoginManager.withAuth { ApiClient.instance.explanationVideo(question) }.getOrThrow()

/**
 * 只在确认「这题确实没有视频」时才调用。服务端按 (租户, 题干) 幂等：
 * 已有成片会直接返回 done，不会重复起 IVH 任务；这里也**不做任何轮询等待**。
 */
suspend fun queueWrongVideo(
    question: String,
    script: String,
    knowledgePoint: String? = null
): ExplanationVideoResp =
    LoginManager.withAuth {
        ApiClient.videoStream.ensure(
            EnsureExplanationVideoReq(question, script, knowledgePoint)
        )
    }.getOrThrow()

/**
 * 题干归一化键，与后端 `_qkey` 对齐（NFKC → 小写 → 去空白/标点）。
 * 只用于错题卡片把已出片视频标成「可播放」，真正取流仍由服务端按题干权威判定。
 */
fun videoQKey(text: String): String {
    val normalized = java.text.Normalizer
        .normalize(text, java.text.Normalizer.Form.NFKC)
        .lowercase()
    return buildString {
        for (ch in normalized) {
            if (ch.isWhitespace()) continue
            val type = Character.getType(ch)
            // Unicode 标点 P*：Pd/Ps/Pe/Pc/Po (20..24) + Pi/Pf (29/30)
            if (type in 20..24 || type == 29 || type == 30) continue
            append(ch)
        }
    }
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
suspend fun downloadExplanationVideo(
    context: Context,
    videoId: String,
    version: String? = null
): File {
    val safeId = videoId.replace(UNSAFE_FILENAME, "_")
    // 文件名带上服务端 updated_at：同一条成片重复点击直接读本地缓存，
    // 教师端 regen 后版本变化会重新下载，不会拿旧片顶掉新片。
    val ver = (version ?: "v1").replace(UNSAFE_FILENAME, "_").take(40)
    val dir = File(context.applicationContext.cacheDir, "explanation_videos")
        .apply { mkdirs() }
    val target = File(dir, "$safeId-$ver.mp4")
    if (target.exists() && target.length() > 0L) return target

    val body: ResponseBody = LoginManager.withAuth {
        ApiClient.videoStream.stream(videoId)
    }.getOrThrow()
    val part = File(dir, "$safeId-$ver.mp4.part")

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
            if (target.exists()) target.delete()
            if (part.renameTo(target)) target else part
        } catch (e: Exception) {
            part.delete()
            throw e
        }
    }
}
