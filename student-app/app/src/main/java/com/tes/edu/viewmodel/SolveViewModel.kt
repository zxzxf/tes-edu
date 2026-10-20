package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.SolveRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

// 解题页 ViewModel：分发意图 -> 调网关/Agent 后端（P0 仅骨架）
class SolveViewModel : ViewModel() {
    private val _answer = MutableStateFlow("")
    val answer: StateFlow<String> = _answer

    fun solve(question: String) {
        viewModelScope.launch {
            runCatching { ApiClient.instance.solve(SolveRequest(question)) }
                .onSuccess { _answer.value = it.answer }
                .onFailure { _answer.value = "请求失败：${it.message}" }
        }
    }
}
