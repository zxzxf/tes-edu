package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.AppContextProvider
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.data.remote.GraphResponse
import com.tes.edu.data.remote.LoginRequest
import com.tes.edu.data.remote.SessionStore
import com.tes.edu.ui.graph.GraphLayout
import com.tes.edu.ui.graph.GraphLayoutCalc
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * T-B5 图谱页状态。
 *
 * 链路：确保登录（占位 /login 换 JWT）-> GET /learning/knowledge-graph
 *      -> 本地分层布局 -> Canvas 渲染。
 */
data class GraphUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val graph: GraphResponse? = null,
    val layout: GraphLayout? = null,
    val selected: GraphNode? = null,
    val studentId: String = DEFAULT_STUDENT_ID,
    // T-G1：节点详情「练这个知识点」——拉题中的加载态与无题/失败提示
    val kpLoading: Boolean = false,
    val kpNotice: String? = null
) {
    companion object {
        /**
         * 演示学生（106 库真实流水 student_001~003；P2 登录态后取当前用户）。
         * 占位账号 student@tes.edu / demo1234（role=student）。
         */
        const val DEFAULT_STUDENT_ID = "student_001"
        const val DEV_USERNAME = "student@tes.edu"
        const val DEV_PASSWORD = "demo1234"
    }
}

class GraphViewModel : ViewModel() {

    private val _ui = MutableStateFlow(GraphUiState())
    val ui: StateFlow<GraphUiState> = _ui.asStateFlow()

    init {
        load()
    }

    fun load(studentId: String = _ui.value.studentId) {
        viewModelScope.launch {
            _ui.update { it.copy(loading = true, error = null, selected = null) }
            runCatching {
                ensureLogin()
                ApiClient.instance.knowledgeGraph(studentId = studentId)
            }.onSuccess { res ->
                val layout = GraphLayoutCalc.layout(res.nodes, res.edges)
                _ui.update {
                    it.copy(
                        loading = false,
                        graph = res,
                        layout = layout,
                        studentId = studentId
                    )
                }
            }.onFailure { e ->
                _ui.update {
                    it.copy(loading = false, error = "加载失败：${e.message}")
                }
            }
        }
    }

    fun select(node: GraphNode?) {
        _ui.update { it.copy(selected = node, kpNotice = null) }
    }

    /**
     * T-G1：从图谱直接开练——按知识点 code 拉真实题目（T-A4 `/learning/variants`，
     * 同点变式优先、下游兜底），取出题干交给调用方跳输入页。
     *
     * 不用 `/api/app/similar` 的 LLM 生成：图谱要的是"这题确实属于该知识点"，
     * 库里的题可回查 content_hash，生成题做不到。
     *
     * @param onPicked 仅在真的拿到题干时回调；无题/失败只更新 `kpNotice`，不跳转。
     */
    fun practiceKp(node: GraphNode, onPicked: (String) -> Unit) {
        viewModelScope.launch {
            _ui.update { it.copy(kpLoading = true, kpNotice = null) }
            runCatching {
                ensureLogin()
                ApiClient.instance.variants(kp = node.code, n = 1)
            }.onSuccess { res ->
                val stem = res.items.firstOrNull()?.stem?.takeIf { it.isNotBlank() }
                if (stem != null) {
                    _ui.update { it.copy(kpLoading = false, kpNotice = null) }
                    onPicked(stem)
                } else {
                    _ui.update {
                        it.copy(
                            kpLoading = false,
                            kpNotice = res.note ?: "该知识点暂无可练题目（题库未覆盖）"
                        )
                    }
                }
            }.onFailure { e ->
                _ui.update {
                    it.copy(kpLoading = false, kpNotice = "拉取题目失败：${e.message}")
                }
            }
        }
    }

    /** 无 token 先登录（占位账号）；登录成功让 ApiClient 重建以带上拦截器。 */
    private suspend fun ensureLogin() {
        val session = SessionStore(AppContextProvider.context)
        if (!session.token.isNullOrBlank()) return
        val res = ApiClient.instance.login(
            LoginRequest(GraphUiState.DEV_USERNAME, GraphUiState.DEV_PASSWORD)
        )
        session.token = res.accessToken
        session.role = res.role
        ApiClient.rebuild()
    }
}
