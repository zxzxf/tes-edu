package com.tes.edu.state

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 跨 tab 的一次性传参桥接（进程内单例）。
 *
 * 底部六个 tab 彼此是独立的 NavBackStackEntry，直接互相传参要把导航图改成
 * 带参路由（参数编码/解码 + 回退栈语义都要重做）。波 1 只需三种"一次性搬运"：
 *
 *  - `lastQuestion`：最近一次真正提交过的题干 —— 触类页据此自动生成变式题；
 *  - `pendingQuestion`：别的页想让学生立刻练的题干 —— 输入页进来就消费并预填；
 *  - `pendingFocusKp/Title`：图谱选中的知识点 —— 背诵页据此限定范围。
 *
 * 全部走 StateFlow 且**消费后即清空**（consume* 系列），避免"跳转一次、之后每次
 * 回到该页都被旧值顶掉"的经典坑。
 */
object AppBridge {

    private val _lastQuestion = MutableStateFlow("")
    val lastQuestion: StateFlow<String> = _lastQuestion.asStateFlow()

    private val _pendingQuestion = MutableStateFlow<String?>(null)
    val pendingQuestion: StateFlow<String?> = _pendingQuestion.asStateFlow()

    private val _pendingFocusKp = MutableStateFlow<String?>(null)
    val pendingFocusKp: StateFlow<String?> = _pendingFocusKp.asStateFlow()

    private val _pendingFocusTitle = MutableStateFlow<String?>(null)
    val pendingFocusTitle: StateFlow<String?> = _pendingFocusTitle.asStateFlow()

    /** 记录"学生最近在做的题"——触类页的来源题干。 */
    fun setLastQuestion(question: String) {
        val q = question.trim()
        if (q.isNotBlank()) _lastQuestion.value = q
    }

    /** 别页请求"去输入页练这道题"。 */
    fun postQuestion(question: String) {
        val q = question.trim()
        if (q.isNotBlank()) _pendingQuestion.value = q
    }

    /** 输入页取出待练题干；取出即清空（只消费一次）。 */
    fun consumeQuestion(): String? {
        val v = _pendingQuestion.value
        _pendingQuestion.value = null
        return v
    }

    /** 图谱请求"去背诵页背这个知识点"。 */
    fun postFocusKp(code: String, title: String? = null) {
        if (code.isBlank()) return
        _pendingFocusKp.value = code
        _pendingFocusTitle.value = title
    }

    /** 背诵页取出聚焦知识点；取出即清空（只消费一次）。 */
    fun consumeFocusKp(): Pair<String, String?>? {
        val code = _pendingFocusKp.value ?: return null
        val title = _pendingFocusTitle.value
        _pendingFocusKp.value = null
        _pendingFocusTitle.value = null
        return code to title
    }

    /** 仅单测用：清空全部桥接状态。 */
    fun resetForTest() {
        _lastQuestion.value = ""
        _pendingQuestion.value = null
        _pendingFocusKp.value = null
        _pendingFocusTitle.value = null
    }
}
