package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.SimilarRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * 触类旁通页状态。
 *
 * @param sourceQuestion 本次变式题的来源题干（展示在顶部，学生可改后重生成）
 * @param items 变式题列表（后端 `similar` 字段，已过滤空白）
 * @param knowledgePoints 来源题命中的知识点（后端 `knowledge_points`，作考点 pill）
 * @param loaded 是否已成功拉过一次——区分"还没生成"与"生成了但为空"
 * @param degraded 后端是否走了检索兜底（LLM 额度受限等）。true 时 items 是
 *                 题库片段而非完整题目，UI 必须说清楚，不能当成可练的题。
 */
data class SimilarUiState(
    val sourceQuestion: String = "",
    val items: List<String> = emptyList(),
    val knowledgePoints: List<String> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val loaded: Boolean = false,
    val degraded: Boolean = false
)

/**
 * 触类旁通（T-S2）：一题做完 -> 拉同型/变式题。
 *
 * 与 SolveViewModel 同构：构造可注入 ApiService（生产走 ApiClient.instance，
 * JVM 单测注入 fake），状态单一出口 `ui`。
 */
class SimilarViewModel(
    private val api: ApiService = ApiClient.instance
) : ViewModel() {

    private val _ui = MutableStateFlow(SimilarUiState())
    val ui: StateFlow<SimilarUiState> = _ui

    /**
     * 生成变式题。
     * @param question 来源题干；空白时给可理解的中文提示而不是发空请求。
     */
    fun load(question: String, count: Int = 3) {
        val q = question.trim()
        if (q.isBlank()) {
            _ui.value = _ui.value.copy(
                loading = false,
                error = "先去「输入」页做一道题，或在这里直接写题干"
            )
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, sourceQuestion = q)
            LoginManager.withAuth {
                api.similar(SimilarRequest(question = q, count = count))
            }.onSuccess { res ->
                _ui.value = _ui.value.copy(
                    loading = false,
                    loaded = true,
                    items = res.similar.map { it.trim() }.filter { it.isNotEmpty() },
                    knowledgePoints = res.knowledgePoints,
                    degraded = res.similarDegraded,
                    error = null
                )
            }.onFailure { e ->
                _ui.value = _ui.value.copy(
                    loading = false,
                    error = "生成变式题失败：${e.message}"
                )
            }
        }
    }

    /** 「换一批」：用同一来源题干重新生成（LLM 生成，内容会变）。 */
    fun refresh(count: Int = 3) = load(_ui.value.sourceQuestion, count)

    /** 学生手动改了来源题干：清掉旧结果，避免展示与输入不一致的列表。 */
    fun clear() {
        _ui.value = SimilarUiState()
    }
}
