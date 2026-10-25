package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.SolveResponse
import com.tes.edu.util.ImageCompressor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

/** 拍照页阶段。 */
enum class CameraStage { Idle, Recognizing, Recognized }

/** 失败分类：不同原因给学生的动作完全不同，不能只显示一句"失败了"。 */
enum class CameraErrorKind {
    /** 后端未配置视觉模型——功能未启用，学生只能改用文字输入。 */
    VlmDisabled,

    /** 图上传了但没识别出文字（拍糊了/空白页）——重拍即可。 */
    EmptyText,

    /** 网络或后端异常——可重试。 */
    Network
}

/**
 * 拍照识题页状态。
 *
 * @param preview 压缩后用于预览与上图的字节（同一份，避免"预览的是 A、传的是 B"）
 * @param recognizedText OCR 抽出的题干，**学生可改**——识别准确率是这个功能最大的
 *                       风险点，所以绝不做"识别即提交"
 */
data class CameraUiState(
    val stage: CameraStage = CameraStage.Idle,
    val preview: ByteArray? = null,
    val recognizedText: String = "",
    val knowledgePoints: List<String> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val errorKind: CameraErrorKind? = null
)

/**
 * 拍照识题（T-P3/T-P4）：压缩 -> 上传 -> 拿识别结果。
 *
 * 与其余 ViewModel 同构（构造注入 ApiService，单测喂 fake），额外的两点设计：
 *
 *  1. **压缩以 `compress` 注入**：[ImageCompressor] 依赖 Android `BitmapFactory`，
 *     JVM 单测里是 "not mocked" 桩。注入成函数后单测可传 identity，
 *     状态机本身就能在 JVM 上完整验证。
 *  2. **不自行解题**：只取 `recognized_text` 交给学生确认，再走输入页的既有
 *     `/solve` 链路——OCR 一旦认错，学生有机会改，而不是直接拿到一份错解析。
 */
class CameraViewModel(
    private val api: ApiService = ApiClient.instance,
    private val compress: suspend (ByteArray) -> ByteArray = { raw ->
        withContext(Dispatchers.Default) { ImageCompressor.compress(raw) }
    }
) : ViewModel() {

    private val _ui = MutableStateFlow(CameraUiState())
    val ui: StateFlow<CameraUiState> = _ui

    /**
     * 拿到一张图（拍照或相册）后先压缩并预览，等学生点「识别这道题」。
     * 放在这里而不是页面里，是为了让"预览的字节"与"上传的字节"是同一份。
     */
    fun prepare(raw: ByteArray) {
        viewModelScope.launch {
            val compressed = runCatching { compress(raw) }.getOrDefault(raw)
            _ui.value = _ui.value.copy(
                stage = CameraStage.Idle,
                preview = compressed,
                recognizedText = "",
                knowledgePoints = emptyList(),
                error = null,
                errorKind = null
            )
        }
    }

    /** 上传当前预览图做识别。 */
    fun recognize() {
        val bytes = _ui.value.preview
        if (bytes == null) {
            _ui.value = _ui.value.copy(error = "先拍一张或从相册选一张题目图")
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(
                stage = CameraStage.Recognizing,
                loading = true,
                error = null,
                errorKind = null
            )
            val part = MultipartBody.Part.createFormData(
                "file",
                "capture.jpg",
                bytes.toRequestBody("image/jpeg".toMediaTypeOrNull())
            )
            runCatching { api.solveImage(part) }
                .onSuccess { res -> applyResult(res) }
                .onFailure { e ->
                    _ui.value = _ui.value.copy(
                        stage = CameraStage.Idle,
                        loading = false,
                        errorKind = CameraErrorKind.Network,
                        error = "上传失败：${e.message ?: "网络异常"}"
                    )
                }
        }
    }

    /** 学生手改识别结果（识别不准时的兜底动作，改完照样能解题）。 */
    fun updateText(text: String) {
        _ui.value = _ui.value.copy(recognizedText = text)
    }

    /** 「重新拍照」：丢掉图与结果，回到初始态。 */
    fun reset() {
        _ui.value = CameraUiState()
    }

    private fun applyResult(res: SolveResponse) {
        val text = res.recognizedText.orEmpty().trim()
        val kind = when {
            // 后端已配视觉模型但没认出文字 / 压根没配 —— 区分对待
            res.vlmEnabled == false -> CameraErrorKind.VlmDisabled
            text.isBlank() -> CameraErrorKind.EmptyText
            else -> null
        }
        val message = when (kind) {
            CameraErrorKind.VlmDisabled ->
                res.message?.takeIf { it.isNotBlank() }
                    ?: "拍照识题暂未启用（未配置视觉模型），请先用文字输入。"
            CameraErrorKind.EmptyText ->
                res.message?.takeIf { it.isNotBlank() }
                    ?: "没从这张图里识别出文字，换一张拍清楚些的。"
            else -> null
        }
        _ui.value = _ui.value.copy(
            stage = if (kind == null) CameraStage.Recognized else CameraStage.Idle,
            loading = false,
            recognizedText = text,
            knowledgePoints = res.knowledgePoints,
            error = message,
            errorKind = kind
        )
    }
}
