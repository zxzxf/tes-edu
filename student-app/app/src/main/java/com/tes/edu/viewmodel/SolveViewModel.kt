package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.Judgement
import com.tes.edu.data.remote.SolveRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

// 解题页状态：S4 过程采集（题干 + 多行作答 -> 判分结果）
data class SolveUiState(
    val answer: String = "",                 // 参考答案（讲解）
    val steps: List<String> = emptyList(),
    val knowledgePoints: List<String> = emptyList(),
    val judgement: Judgement? = null,        // judge v2 步骤级归因
    val loading: Boolean = false,
    val error: String? = null
)

class SolveViewModel : ViewModel() {
    private val _ui = MutableStateFlow(SolveUiState())
    val ui: StateFlow<SolveUiState> = _ui

    /**
     * 提交解题请求。
     * @param process 学生的作答/过程文本；为空时后端返回 need_process=true 要求补过程，不判分。
     */
    fun solve(question: String, process: String? = null) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, judgement = null)
            runCatching {
                ApiClient.instance.solve(
                    SolveRequest(
                        question = question,
                        studentAnswer = process?.takeIf { it.isNotBlank() }
                    )
                )
            }.onSuccess { res ->
                _ui.value = _ui.value.copy(
                    answer = res.answer,
                    steps = res.steps,
                    knowledgePoints = res.knowledgePoints,
                    judgement = res.judgement,
                    loading = false
                )
            }.onFailure {
                _ui.value = _ui.value.copy(
                    loading = false,
                    error = "请求失败：${it.message}"
                )
            }
        }
    }
}
