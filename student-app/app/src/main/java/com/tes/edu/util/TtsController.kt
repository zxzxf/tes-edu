package com.tes.edu.util

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

/**
 * 封装 Android 自带 [TextToSpeech]，用于讲解页「听讲解」语音试听。
 *
 * 特性：
 * - 零后端、零联网，使用设备内置离线中文语音，契合私有化部署合规；
 * - 异步初始化，就绪 / 朗读状态通过 [onState] 回调上抛给 Compose 状态；
 * - 必须在页面离开时调用 [shutdown]（用 `DisposableEffect`）释放引擎，避免泄漏。
 *
 * 若设备未安装中文语音包（[TextToSpeech.LANG_NOT_SUPPORTED] /
 * [TextToSpeech.LANG_MISSING_DATA]），[isReady] 保持 false，调用方应禁用朗读按钮
 * 并提示用户去系统「语言和输入法 → 文字转语音(TTS)」安装中文语音。
 */
class TtsController(context: Context) {

    private var tts: TextToSpeech? = null
    var isReady: Boolean = false
        private set
    var isSpeaking: Boolean = false
        private set

    /** 状态变化回调：参数分别为 (就绪, 朗读中)。 */
    var onState: ((ready: Boolean, speaking: Boolean) -> Unit)? = null

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            if (status == TextToSpeech.SUCCESS) {
                val lang = tts?.setLanguage(Locale.SIMPLIFIED_CHINESE)
                    ?: TextToSpeech.LANG_NOT_SUPPORTED
                isReady = lang != TextToSpeech.LANG_NOT_SUPPORTED &&
                    lang != TextToSpeech.LANG_MISSING_DATA
            }
            onState?.invoke(isReady, isSpeaking)
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                isSpeaking = true
                onState?.invoke(isReady, isSpeaking)
            }

            override fun onDone(utteranceId: String?) {
                isSpeaking = false
                onState?.invoke(isReady, isSpeaking)
            }

            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                isSpeaking = false
                onState?.invoke(isReady, isSpeaking)
            }
        })
    }

    /** 朗读文本。仅在就绪且文本非空时生效，返回是否成功开始。 */
    fun speak(text: String): Boolean {
        if (!isReady || text.isBlank()) return false
        val id = "explain_${System.nanoTime()}"
        return tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.SUCCESS
    }

    /** 停止当前朗读。 */
    fun stop() {
        tts?.stop()
        isSpeaking = false
        onState?.invoke(isReady, isSpeaking)
    }

    /** 释放 TTS 引擎，务必在页面销毁时调用。 */
    fun shutdown() {
        try {
            tts?.stop()
        } catch (_: Exception) {
        }
        tts?.shutdown()
        tts = null
        isReady = false
        isSpeaking = false
    }
}
