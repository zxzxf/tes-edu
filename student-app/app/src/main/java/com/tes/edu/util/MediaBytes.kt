package com.tes.edu.util

import android.content.Context
import android.graphics.Bitmap
import java.io.ByteArrayOutputStream

/**
 * M1-6 答题附图：把 content Uri / 相机缩略图字节读成可上传的 JPEG。
 *
 * 独立成 object（而不是塞进 ViewModel）：与 [ImageCompressor] 同理——
 * Android framework 类在 JVM 单测里是桩，ViewModel 只接收 ByteArray，
 * 「Uri → bytes」这一步留在 UI 层，单测不需要碰。
 */
object MediaBytes {

    /** 从 content Uri 读全量字节（PhotoPicker 选图回调后用）。 */
    fun read(context: Context, uri: android.net.Uri): ByteArray? = runCatching {
        context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
    }.getOrNull()?.takeIf { it.isNotEmpty() }

    /** 相机 App 只回缩略图（无 EXTRA_OUTPUT）时的兜底编码。 */
    fun encodeThumb(bitmap: Bitmap): ByteArray = ByteArrayOutputStream().use { out ->
        bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out)
        out.toByteArray()
    }
}
