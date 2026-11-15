package com.tes.edu.data.remote

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.ResponseBody
import retrofit2.http.GET
import retrofit2.http.Path
import retrofit2.http.Streaming
import java.io.File

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
