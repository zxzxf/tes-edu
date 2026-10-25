package com.tes.edu.util

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import androidx.core.content.FileProvider
import java.io.File

/**
 * 构造「调起系统相机、把照片回写到 [file]」的 Intent。
 *
 * 为什么不直接用 `ActivityResultContracts.TakePicture()`：那个官方 contract 内部
 * 只有一行 `Intent(ACTION_IMAGE_CAPTURE).putExtra(EXTRA_OUTPUT, uri)`，带来两个坑——
 *
 *  1. **不检查有没有 App 能接这个 Intent**：无相机的平板、模拟器、被 MDM 掐掉相机的
 *     工作资料上直接抛 ActivityNotFoundException，落到 UI 就是一句没有出路的红字。
 *  2. **不给 Uri 授予读写权限**：相机 App 拿到的 `content://` Uri 没有 grant 时，
 *     不少 ROM（尤其国产定制相机）写不进文件，表现为相机一闪而过 / 直接崩 / 不回调。
 *
 * 这里两步都补上：先 `queryIntentActivities` 预检，再对**所有候选**显式
 * `grantUriPermission`。
 *
 * @return 可直接 startActivity 的 Intent；**返回 null 表示这台设备上没有任何 App
 *         能接 ACTION_IMAGE_CAPTURE**，调用方应回落相册而不是报错。
 *
 * @throws IllegalStateException FileProvider 取不到 Uri——这是**配置问题**
 *         （authority 写错 / file_paths.xml 没覆盖该目录），不是设备缺相机，
 *         必须让它炸出来而不是被静默吞掉，否则排查时会误判成"用户没相机"。
 */
fun buildCaptureIntent(
    context: Context,
    file: File,
    authority: String = "${context.packageName}.fileprovider"
): Intent? {
    val uri: Uri = runCatching { FileProvider.getUriForFile(context, authority, file) }
        .getOrElse { e ->
            throw IllegalStateException("FileProvider 取 Uri 失败（authority=$authority, file=$file）", e)
        }

    val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
        .putExtra(MediaStore.EXTRA_OUTPUT, uri)

    // Android 11+ 起必须先在 manifest 的 <queries> 里声明该 action，否则这里恒为空
    val targets = context.packageManager
        .queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)
    if (targets.isEmpty()) return null

    val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
    intent.addFlags(flags)
    // 逐个候选授权：Intent 上的 flag 只作用于"最终被选中的"那个 Activity，
    // 而系统选择器 / 部分 ROM 会在多个相机 App 之间跳转，逐个 grant 才稳。
    targets.forEach { resolveInfo ->
        runCatching {
            context.grantUriPermission(resolveInfo.activityInfo.packageName, uri, flags)
        }
    }
    return intent
}

/**
 * 这台设备能否拍照（用于 UI 决定要不要展示「拍照」按钮）。
 *
 * 与 [buildCaptureIntent] 用同一套判定，避免出现"按钮显示了但点下去回落相册"的错位。
 */
fun canCapture(context: Context): Boolean {
    val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
    return context.packageManager
        .queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)
        .isNotEmpty()
}
