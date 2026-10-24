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
    val studentId: String = DEFAULT_STUDENT_ID
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
        _ui.update { it.copy(selected = node) }
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
