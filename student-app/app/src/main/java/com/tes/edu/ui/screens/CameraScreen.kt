package com.tes.edu.ui.screens

import android.graphics.BitmapFactory
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CenterFocusWeak
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.PhotoLibrary
import androidx.compose.material.icons.rounded.RestartAlt
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.compose.runtime.collectAsState
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.state.AppBridge
import com.tes.edu.ui.components.GradientButton
import com.tes.edu.ui.components.InfoPill
import com.tes.edu.ui.components.SoftCard
import com.tes.edu.ui.components.SoftFieldCard
import com.tes.edu.ui.components.SoftNotice
import com.tes.edu.ui.components.SoftOutlinedButton
import com.tes.edu.ui.components.TesHero
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.viewmodel.CameraErrorKind
import com.tes.edu.viewmodel.CameraStage
import com.tes.edu.viewmodel.CameraViewModel
import java.io.File

/**
 * 拍照识题（T-P2/T-P4）。
 *
 * 技术选型：**系统相机 Intent**（用户拍板，不做 CameraX）——
 * `ActivityResultContracts.TakePicture()` 由相机 App 自己持有相机权限，
 * 本应用**无需动态申请 CAMERA 权限**，也就没有"拒绝授权后功能半死"的状态；
 * 需要处理的只有"设备上没有相机 App"（`ActivityNotFoundException`）。
 *
 * 关键交互取舍：**识别结果一定可编辑，绝不识别即提交**。
 * 拍照识题的准确率（尤其公式与手写）是这个功能的头号风险，
 * 所以流程停在"把识别到的题干给学生看一眼"，确认无误再跳输入页解题。
 */
@Composable
fun CameraScreen(
    vm: CameraViewModel = viewModel(),
    onNavigate: (String) -> Unit = {}
) {
    val ui by vm.ui.collectAsState()
    val ctx = LocalContext.current
    // 相机回写的文件：TakePicture 只回 Boolean，得自己记住写到了哪
    var pendingFile by remember { mutableStateOf<File?>(null) }
    // 本地可恢复的错误（无相机 App / 读图失败），不进 ViewModel——它们与上传无关
    var localError by remember { mutableStateOf<String?>(null) }

    val takePicture = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { ok ->
        val f = pendingFile
        when {
            !ok -> Unit                                  // 学生主动取消，不打扰
            f == null || !f.exists() || f.length() == 0L ->
                localError = "没拿到照片，重新拍一次试试"
            else -> {
                localError = null
                vm.prepare(f.readBytes())
            }
        }
    }

    val pickImage = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        val bytes = runCatching {
            ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
        }.getOrNull()
        if (bytes == null || bytes.isEmpty()) {
            localError = "读不到这张图，换一张试试"
        } else {
            localError = null
            vm.prepare(bytes)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))
        TesHero(
            title = "拍照识题",
            subtitle = "拍下题目，先看看识别得对不对",
            badge = "OCR → 解题"
        )
        Spacer(Modifier.height(18.dp))

        localError?.let {
            SoftNotice(
                text = it,
                icon = Icons.Rounded.CenterFocusWeak,
                color = TesEduColors.Danger,
                bg = TesEduColors.DangerSoft
            )
            Spacer(Modifier.height(12.dp))
        }

        when (ui.stage) {
            CameraStage.Idle -> IdleBlock(
                preview = ui.preview,
                error = ui.error,
                errorKind = ui.errorKind,
                onTake = {
                    val f = File(File(ctx.cacheDir, "images").apply { mkdirs() }, "capture_${System.currentTimeMillis()}.jpg")
                    pendingFile = f
                    val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.fileprovider", f)
                    runCatching { takePicture.launch(uri) }
                        .onFailure {
                            localError = "这台设备上没有可用的相机应用，请从相册选图"
                            Toast.makeText(ctx, localError, Toast.LENGTH_SHORT).show()
                        }
                },
                onPick = {
                    runCatching { pickImage.launch("image/*") }
                        .onFailure { localError = "打不开相册，试试直接拍照" }
                },
                onRecognize = { vm.recognize() },
                onRetake = { vm.reset() },
                onUseText = { onNavigate(Screen.Input.route) }
            )

            CameraStage.Recognizing -> {
                SoftCard {
                    Column(
                        Modifier.fillMaxWidth().padding(vertical = 8.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(28.dp),
                            color = TesEduColors.Brand
                        )
                        Spacer(Modifier.height(12.dp))
                        Text(
                            "正在识别题面…",
                            style = MaterialTheme.typography.bodyMedium,
                            color = TesEduColors.TextMuted
                        )
                    }
                }
            }

            CameraStage.Recognized -> ResultBlock(
                text = ui.recognizedText,
                knowledgePoints = ui.knowledgePoints,
                onTextChange = { vm.updateText(it) },
                onSolve = {
                    AppBridge.postQuestion(ui.recognizedText)
                    onNavigate(Screen.Input.route)
                },
                onRetake = { vm.reset() }
            )
        }

        Spacer(Modifier.height(24.dp))
    }
}

/** 未开始 / 已选图待识别 / 失败：三种都停在这一屏，按有没有预览图分支。 */
@Composable
private fun IdleBlock(
    preview: ByteArray?,
    error: String?,
    errorKind: CameraErrorKind?,
    onTake: () -> Unit,
    onPick: () -> Unit,
    onRecognize: () -> Unit,
    onRetake: () -> Unit,
    onUseText: () -> Unit
) {
    if (error != null) {
        SoftNotice(
            text = error,
            icon = Icons.Rounded.CenterFocusWeak,
            color = when (errorKind) {
                CameraErrorKind.VlmDisabled -> TesEduColors.Amber
                else -> TesEduColors.Danger
            },
            bg = when (errorKind) {
                CameraErrorKind.VlmDisabled -> TesEduColors.AmberSoft
                else -> TesEduColors.DangerSoft
            }
        )
        Spacer(Modifier.height(12.dp))
        // 未启用视觉模型时重拍没有意义，直接引导回文字输入
        if (errorKind == CameraErrorKind.VlmDisabled) {
            GradientButton(text = "改用文字输入", icon = Icons.Rounded.EditNote) { onUseText() }
            Spacer(Modifier.height(10.dp))
        } else {
            SoftOutlinedButton(text = "换一张图", icon = Icons.Rounded.RestartAlt) { onRetake() }
            Spacer(Modifier.height(10.dp))
        }
    }

    if (preview == null) {
        SoftCard {
            Text(
                "把题目拍清楚，我来认题面",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "建议正对题目、光线均匀，避开手指和阴影。识别结果会先给你看，" +
                    "认错了可以直接改，不会拿去乱解。",
                style = MaterialTheme.typography.bodySmall,
                color = TesEduColors.TextMuted
            )
            Spacer(Modifier.height(16.dp))
            GradientButton(text = "拍照", icon = Icons.Rounded.PhotoCamera) { onTake() }
            Spacer(Modifier.height(10.dp))
            SoftOutlinedButton(text = "从相册选图", icon = Icons.Rounded.PhotoLibrary) { onPick() }
        }
        return
    }

    // 已选图：先确认拍得清楚，再决定要不要花一次识别
    SoftCard {
        PreviewImage(bytes = preview)
        Spacer(Modifier.height(14.dp))
        GradientButton(text = "识别这道题", icon = Icons.Rounded.CenterFocusWeak) { onRecognize() }
        Spacer(Modifier.height(10.dp))
        SoftOutlinedButton(text = "重拍 / 换一张", icon = Icons.Rounded.RestartAlt) { onRetake() }
    }
}

/**
 * 预览图。字节来自 ViewModel 的 `preview`（与上传的是同一份），
 * 这里只做解码；解码不出来就退化成一行提示，不让页面崩。
 */
@Composable
private fun PreviewImage(bytes: ByteArray?) {
    if (bytes == null) return
    val bitmap = remember(bytes) {
        runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }.getOrNull()
    }
    if (bitmap == null) {
        Text(
            "这张图预览不出来，但还能试着识别",
            style = MaterialTheme.typography.bodySmall,
            color = TesEduColors.TextMuted
        )
        return
    }
    Image(
        bitmap = bitmap.asImageBitmap(),
        contentDescription = "待识别的题目图",
        contentScale = ContentScale.Fit,
        modifier = Modifier
            .fillMaxWidth()
            .height(220.dp)
            .clip(RoundedCornerShape(14.dp))
    )
}

/** 识别结果：可编辑题干 + 知识点 + 两个出口。 */
@Composable
private fun ResultBlock(
    text: String,
    knowledgePoints: List<String>,
    onTextChange: (String) -> Unit,
    onSolve: () -> Unit,
    onRetake: () -> Unit
) {
    SoftCard {
        Text(
            "识别到的题面",
            style = MaterialTheme.typography.titleSmall,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "认错了直接改，改完照样能解",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
        Spacer(Modifier.height(12.dp))
        SoftFieldCard(
            label = "题目",
            hint = "可编辑",
            value = text,
            onValueChange = onTextChange,
            placeholder = "把题目放进来…",
            minLines = 4,
            leadingIcon = Icons.Rounded.EditNote
        )
        if (knowledgePoints.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                knowledgePoints.take(3).forEach { kp ->
                    InfoPill(text = kp, color = TesEduColors.Mint, bg = TesEduColors.MintSoft)
                }
            }
        }
        Spacer(Modifier.height(16.dp))
        GradientButton(
            text = "就用这道题去解",
            icon = Icons.Rounded.CenterFocusWeak,
            enabled = text.isNotBlank()
        ) { onSolve() }
        Spacer(Modifier.height(10.dp))
        SoftOutlinedButton(text = "重新拍照", icon = Icons.Rounded.RestartAlt) { onRetake() }
    }
}
