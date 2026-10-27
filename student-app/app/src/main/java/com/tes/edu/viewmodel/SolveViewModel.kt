package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.Judgement
import com.tes.edu.data.remote.SolveRequest
import com.tes.edu.data.remote.SolveResponse
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

// 解题页状态：S4 过程采集（题干 + 多行作答 -> 判分结果）
// T-C2/C3：Hint 阶梯对话（L0–L4）+ 题型路由 + 分步拆解 + "直接看解析"
data class SolveUiState(
    val answer: String = "",                 // 参考答案（讲解）
    val steps: List<String> = emptyList(),
    val knowledgePoints: List<String> = emptyList(),
    val judgement: Judgement? = null,        // judge v2 步骤级归因
    val loading: Boolean = false,
    val error: String? = null,
    // T-C3 题型路由（首轮响应带回，决定引导策略与按钮展示）
    val questionTier: String? = null,        // memory / medium / finale
    val questionTierName: String? = null,    // 记忆型 / 中等 / 压轴
    val questionType: String? = null,        // 单选/多选/填空/解答/实验
    val hintMaxRounds: Int? = null,          // 引导轮次上限
    // T-C2/C3 Hint 阶梯状态（ hint=true 的轮次刷新）
    val hintActive: Boolean = false,         // 是否处于阶梯对话中
    val hintLevel: Int? = null,              // 0–4
    val hintLevelName: String? = null,       // 点考点/给方向/给关键公式/给该步推导/完整解析+复盘
    val hintFinale: Boolean = false,         // 压轴题（封顶 L3，L4 只能主动看解析）
    val hintDirectReveal: Boolean = false,   // 本轮是否直达 L4
    val hintRounds: Int? = null,             // 已进行轮次（对照上限展示进度）
    // T-C3 分步拆解（压轴题）
    val hintStep: Int? = null,
    val hintStepTotal: Int? = null,
    val hintStepText: String? = null
) {
    /** "直接看解析"常驻按钮的展示条件：已进入引导对话且未直达过 L4。 */
    val showDirectReveal: Boolean
        get() = hintActive && !hintDirectReveal
}

class SolveViewModel(
    // T-C3：可注入 ApiService——生产走 ApiClient.instance（无参构造，Android 反射实例化），
    // JVM 单测注入 fake（mock 走通"直接看解析"链路）
    private val api: ApiService = ApiClient.instance
) : ViewModel() {
    private val _ui = MutableStateFlow(SolveUiState())
    val ui: StateFlow<SolveUiState> = _ui

    /**
     * 提交解题请求（非阶梯轮：三档路由 + 讲解/判分）。
     * @param process 学生的作答/过程文本；为空时后端返回 need_process=true 要求补过程，不判分。
     */
    fun solve(question: String, process: String? = null) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, judgement = null)
            LoginManager.withAuth {
                api.solve(
                    SolveRequest(
                        question = question,
                        studentAnswer = process?.takeIf { it.isNotBlank() }
                    )
                )
            }.onSuccess { res -> _ui.value = _ui.value.applyResponse(res, loading = false) }
             .onFailure { _ui.value = _ui.value.copy(loading = false, error = "请求失败：${it.message}") }
        }
    }

    /**
     * T-C2/C3：发起 Hint 阶梯对话（首轮 L0 点考点）。
     * 阶梯只存在于引导档——由后端保证。
     */
    fun startHint(question: String) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null)
            LoginManager.withAuth {
                api.solve(SolveRequest(question = question, hint = true))
            }.onSuccess { res -> _ui.value = _ui.value.applyResponse(res, loading = false, hintActive = true) }
             .onFailure { _ui.value = _ui.value.copy(loading = false, error = "请求失败：${it.message}") }
        }
    }

    /**
     * T-C2/C3：阶梯对话中提交学生回应。
     * 压轴题的有实质回应会推进到下一步（一步一 Hint）；
     * 连续挫败（"我不懂/直接说吧"）会跳一级 Hint。
     */
    fun replyHint(question: String, reply: String) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null)
            LoginManager.withAuth {
                api.solve(
                    SolveRequest(question = question, hint = true, studentReply = reply)
                )
            }.onSuccess { res -> _ui.value = _ui.value.applyResponse(res, loading = false, hintActive = true) }
             .onFailure { _ui.value = _ui.value.copy(loading = false, error = "请求失败：${it.message}") }
        }
    }

    /**
     * T-C3 "直接看解析"一等功能：直达 L4 完整解析 + 复盘三问。
     * App 侧常驻按钮 + 一次轻确认后调用；点击行为由后端入埋点
     * （event_type=direct_reveal）且不作为违规（R6）。
     */
    fun revealDirect(question: String) {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null)
            LoginManager.withAuth {
                api.solve(SolveRequest(question = question, mode = "direct"))
            }.onSuccess { res -> _ui.value = _ui.value.applyResponse(res, loading = false, hintActive = true) }
             .onFailure { _ui.value = _ui.value.copy(loading = false, error = "请求失败：${it.message}") }
        }
    }

    /**
     * T-S4/T-G1：清空本次结果，准备接一道新题。
     * 触类「再练这道」与图谱「练这个知识点」投递新题干时调用——
     * 不清会把上一题的答案/判分卡留在新题干下面。
     */
    fun reset() {
        _ui.value = SolveUiState()
    }

    private fun SolveUiState.applyResponse(
        res: SolveResponse,
        loading: Boolean,
        hintActive: Boolean = this.hintActive
    ): SolveUiState = copy(
        answer = res.answer,
        steps = res.steps,
        knowledgePoints = res.knowledgePoints,
        judgement = res.judgement,
        loading = loading,
        questionTier = res.questionTier ?: questionTier,
        questionTierName = res.questionTierName ?: questionTierName,
        questionType = res.questionType ?: questionType,
        hintMaxRounds = res.hintMaxRounds ?: hintMaxRounds,
        hintActive = hintActive,
        hintLevel = res.hintLevel ?: hintLevel,
        hintLevelName = res.hintLevelName ?: hintLevelName,
        hintFinale = res.hintFinale ?: hintFinale,
        hintDirectReveal = res.hintDirectReveal ?: hintDirectReveal,
        hintRounds = res.hintRounds ?: hintRounds,
        hintStep = res.hintStep ?: hintStep,
        hintStepTotal = res.hintStepTotal ?: hintStepTotal,
        hintStepText = res.hintStepText ?: hintStepText
    )
}
