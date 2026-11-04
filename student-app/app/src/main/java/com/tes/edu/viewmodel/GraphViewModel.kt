package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.GraphEdge
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.data.remote.GraphResponse
import com.tes.edu.data.remote.StudentSession
import com.tes.edu.ui.graph.GraphLayout
import com.tes.edu.ui.graph.GraphLayoutCalc
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * T-B5 图谱页状态。
 *
 * 链路：确保登录（占位 /login 换 JWT）-> 读 StudentSession.currentId()
 *      -> GET /learning/knowledge-graph -> 本地分层布局 -> Canvas 渲染。
 */
data class GraphUiState(
    val loading: Boolean = false,
    val error: String? = null,
    val graph: GraphResponse? = null,
    val layout: GraphLayout? = null,
    val selected: GraphNode? = null,
    /** 实际用于查询的登录身份；未取到登录态时为 null（不发请求）。 */
    val studentId: String? = null,
    /** M3-4 按卷维度：非空时看的是「这一卷的知识点 + 前驱后继」，不是全量图谱。 */
    val paperAssignmentId: Long? = null,
    val paperTitle: String = "",
    // T-G1：节点详情「练这个知识点」——拉题中的加载态与无题/失败提示
    val kpLoading: Boolean = false,
    val kpNotice: String? = null
)

/**
 * 图谱加载短超时：paperGraph / knowledgeGraph 本应 2s 内返回，但真机走跨境 Cloudflare
 * 隧道时偶发「连接成功、响应读取挂起」。OkHttp 全局 readTimeout 是 180s（为解题 LLM
 * 生成放宽），不能让图谱干等 3 分钟。这里 30s 内无响应即失败走 error 态（可重试）。
 */
private const val GRAPH_TIMEOUT_MS = 30_000L

class GraphViewModel(
    // 可注入：JVM 单测用 fake 记录 knowledgeGraph 的 student_id；生产走 ApiClient。
    // 全默认参数会生成无参构造，Compose 的 viewModel() 仍可反射实例化（同 SolveViewModel）。
    private val api: ApiService = ApiClient.instance,
    // 身份统一入口：与作业/错题/背诵一样读登录态；单测可替换成已登录用户。
    private val resolveStudentId: () -> String? = { StudentSession.currentId() }
) : ViewModel() {

    private val _ui = MutableStateFlow(GraphUiState())
    val ui: StateFlow<GraphUiState> = _ui.asStateFlow()

    init {
        load()
    }

    /**
     * M3-4 按卷加载：`GET /api/student/exams/{assignment_id}/graph`。
     *
     * 返回体与全量图谱同构（nodes/edges/summary），所以复用同一套布局与渲染；
     * 差别只在节点集合（卷内点 + 前驱 + 后继）和 `paper_kp_count` 提示。
     * 该接口只按 assignment_id + JWT 取数，客户端不传 student_id，身份天然是登录态。
     */
    fun loadPaper(assignmentId: Long, title: String = "") {
        viewModelScope.launch {
            _ui.update {
                it.copy(
                    loading = true,
                    error = null,
                    selected = null,
                    paperAssignmentId = assignmentId,
                    paperTitle = title
                )
            }
            runCatching {
                // 短超时：避免被全局 180s readTimeout 卡死（真机隧道偶发读取挂起）
                withTimeoutOrNull(GRAPH_TIMEOUT_MS) {
                    ensureLogin()
                    ApiClient.exam.paperGraph(assignmentId)
                } ?: throw java.net.SocketTimeoutException("图谱加载超时（网络不佳，请重试）")
            }.onSuccess { dto ->
                // onSuccess 内的解析/布局也兜底：任一异常走 error 态而非 FC
                runCatching {
                    val res = dto.toGraphResponse()
                    val layout = GraphLayoutCalc.layout(res.nodes, res.edges)
                    _ui.update {
                        it.copy(
                            loading = false,
                            graph = res,
                            layout = layout,
                            paperAssignmentId = assignmentId,
                            paperTitle = title
                        )
                    }
                }.onFailure { e ->
                    _ui.update { it.copy(loading = false, error = "图谱处理失败：${e.message}") }
                }
            }.onFailure { e ->
                _ui.update { it.copy(loading = false, error = "加载失败：${e.message}") }
            }
        }
    }

    /** 退出按卷维度，回到全量图谱。 */
    fun exitPaper() {
        val s = _ui.value
        if (s.paperAssignmentId == null) return
        _ui.update { it.copy(paperAssignmentId = null, paperTitle = "") }
        load()
    }

    /**
     * 全量图谱：身份一律取登录态，不再写死演示学号 student_001。
     *
     * 后端 learning/knowledge-graph 不按 JWT 覆盖 student_id（与错题/背诵不同），
     * 客户端传什么就查谁；所以必须 ensureLogin 之后再解析身份，且取不到登录态时
     * 宁可报错，也不发一个「可能查到别人学情」的请求。
     */
    fun load() {
        viewModelScope.launch {
            _ui.update { it.copy(loading = true, error = null, selected = null) }
            runCatching {
                withTimeoutOrNull(GRAPH_TIMEOUT_MS) {
                    ensureLogin()
                    val sid = resolveStudentId() ?: error("登录态未就绪，请重试")
                    sid to api.knowledgeGraph(studentId = sid)
                } ?: throw java.net.SocketTimeoutException("图谱加载超时（网络不佳，请重试）")
            }.onSuccess { (sid, res) ->
                runCatching {
                    val layout = GraphLayoutCalc.layout(res.nodes, res.edges)
                    _ui.update {
                        it.copy(
                            loading = false,
                            graph = res,
                            layout = layout,
                            studentId = sid,
                            paperAssignmentId = null,
                            paperTitle = ""
                        )
                    }
                }.onFailure { e ->
                    _ui.update { it.copy(loading = false, error = "图谱处理失败：${e.message}") }
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
                api.variants(kp = node.code, n = 1)
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
        // 统一走 LoginManager（输入/触类/拍照模块共用）；登录失败抛错进 error 态
        if (!LoginManager.ensureLogin()) error("登录失败，请检查网络后重试")
    }
}
