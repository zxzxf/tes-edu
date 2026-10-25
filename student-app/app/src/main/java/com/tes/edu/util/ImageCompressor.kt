package com.tes.edu.util

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayOutputStream
import kotlin.math.max

/**
 * 拍照/选图后的本地压缩（T-P3）。
 *
 * 目的不是"好看"，是**让上传可控**：手机直出照片动辄 3~6 MB，走公网入口既慢又容易
 * 撞网关的请求体上限，而 OCR 只需要看清文字，1600px 长边 + JPEG 85 完全够用
 * （实测压缩后 150~400 KB）。
 *
 * ⚠️ 这是**纯 Android 实现**——`BitmapFactory` 在 JVM 单测里是 "not mocked" 桩，
 * 会直接抛异常。因此 CameraViewModel 把它当 `suspend (ByteArray) -> ByteArray`
 * 注入，单测传 identity，ViewModel 状态机才能在 JVM 上跑（见 CameraViewModelTest）。
 */
object ImageCompressor {

    /** 长边上限：够 OCR 认公式，又不至于让上传变慢。 */
    const val MAX_EDGE = 1600

    /** 起始 JPEG 质量；仍超 [MAX_BYTES] 时按档下调。 */
    const val QUALITY = 85

    /** 目标体积上限（800 KB）；网关与后端都不该为一张图扛几 MB。 */
    const val MAX_BYTES = 800 * 1024

    /**
     * 压缩给定图片字节；任何一步失败（非图片/解码失败）都**原样返回**，
     * 让上层照常上传——总比直接判失败、让学生不知为何好。
     */
    fun compress(
        bytes: ByteArray,
        maxEdge: Int = MAX_EDGE,
        quality: Int = QUALITY,
        maxBytes: Int = MAX_BYTES
    ): ByteArray {
        if (bytes.isEmpty()) return bytes

        val w: Int
        val h: Int
        run {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
            w = bounds.outWidth
            h = bounds.outHeight
        }
        if (w <= 0 || h <= 0) return bytes

        val opts = BitmapFactory.Options().apply {
            inSampleSize = sampleSize(w, h, maxEdge)
            // 不降色彩深度：题目图里的彩色图表/红笔批改也是信息，OCR 用得上
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }
        val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts) ?: return bytes

        return runCatching { encodeUnder(bitmap, quality, maxBytes) }
            .getOrDefault(bytes)
    }

    /** 按长边算采样率（2 的幂，解码器要求）。 */
    private fun sampleSize(w: Int, h: Int, maxEdge: Int): Int {
        val longEdge = max(w, h)
        var sample = 1
        while (longEdge / (sample * 2) >= maxEdge) sample *= 2
        return sample
    }

    /** 编码为 JPEG；超过体积上限就降质量重试（最低 50，再低公式笔画会糊）。 */
    private fun encodeUnder(bitmap: Bitmap, quality: Int, maxBytes: Int): ByteArray {
        var q = quality
        var out = encode(bitmap, q)
        while (out.size > maxBytes && q > 50) {
            q -= 10
            out = encode(bitmap, q)
        }
        return out
    }

    private fun encode(bitmap: Bitmap, quality: Int): ByteArray =
        ByteArrayOutputStream().use { buf ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, quality, buf)
            buf.toByteArray()
        }
}
