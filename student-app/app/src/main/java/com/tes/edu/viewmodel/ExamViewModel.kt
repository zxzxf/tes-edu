package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.local.ExamAnswerCodec
import com.tes.edu.data.local.ExamDraftStore
import com.tes.edu.data.local.RoomExamDraftStore
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.ExamApi
import com.tes.edu.data.remote.ExamQuestionView
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.ExamSubmitItem
import com.tes.edu.data.remote.ExamSubmitRequest
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.StudentExamItem
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.util.UUID

/** 作业页三个层次：列表 → 答题 → 结果。用状态切换而不是三条导航路由，
 *  这样底部导航始终停在「作业」tab 上，返回栈不会堆出一串卷子。 */
enum class ExamStage { LIST, ANSWERING, RESULT }

/** 断网时的作答提示：草稿已落盘，不该让学生以为答案丢了。 */
const val EXAM_OFFLINE_HINT = "网络不可用，作答已保存在本机，联网后可继续"

data class ExamUiState(
    val stage: ExamStage = ExamStage.LIST,
    val loading: Boolean = false,
    val submitting: Boolean = false,
    val error: String? = null,
    val offline: Boolean = false,
    val exams: List<StudentExamItem> = emptyList(),
    /** 列表是否成功加载过：用来区分「空作业」与「还没加载」。 */
    val loaded: Boolean = false,
    val assignmentId: Long = 0,
    val paperId: Long = 0,
    val title: String = "",
    val paperType: String = "",
    val questions: List<ExamQuestionView> = emptyList(),
    val answers: Map<Int, String> = emptyMap(),
    val index: Int = 0,
    /** 本次进入答题页是否恢复了上次草稿（UI 给一句「已恢复上次作答」）。 */
    val draftRestored: Boolean = false,
    val result: ExamResultDto? = null
) {
    val total: Int get() = questions.size
    val current: ExamQuestionView? get() = questions.getOrNull(index)
    val progressText: String get() = "${if (total == 0) 0 else index + 1} / $total"
    val answeredCount: Int get() = questions.count { !answers[it.seq].isNullOrBlank() }
    val unansweredCount: Int get() = (total - answeredCount).coerceAtLeast(0)
    val currentAnswer: String get() = current?.let { answers[it.seq] } ?: ""
    val canSubmit: Boolean get() = total > 0 && !submitting
}

/**
 * 作业闭环学生端（M1-6）：取卷 → 结构化作答 → 提交判分 → 回看。
 *
 * 三条值得记住的约定：
 *  1. **答案全在本地草稿里**。每次点选/输入都落盘（Room），断网、切页、杀进程都不丢；
 *     打开卷子时草稿优先于空白，并把光标停在第一道没答的题上。
 *  2. **client_id 跟着草稿走**。提交重试复用同一个值才有幂等意义；每次新生成一个
 *     等于放弃幂等，补传队列上线后就会重复交卷。
 *  3. **提交成功才删草稿**。失败（含断网）保留草稿并明确告诉学生"答案还在"。
 */
class ExamViewModel(
    private val api: ExamApi = ApiClient.exam,
    private val drafts: ExamDraftStore = RoomExamDraftStore()
) : ViewModel() {

    private val _ui = MutableStateFlow(ExamUiState())
    val ui: StateFlow<ExamUiState> = _ui.asStateFlow()

    /** 当前卷的幂等键，随草稿持久化；换卷时重新取。 */
    private var clientId: String = ""

    // ------------------------------------------------------------------ 列表
    fun refresh() {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, offline = false)
            val res = withAuth { api.list() }
            _ui.value = res.fold(
                onSuccess = { list ->
                    _ui.value.copy(
                        loading = false,
                        exams = list,
                        loaded = true,
                        error = null,
                        offline = false
                    )
                },
                onFailure = { e ->
                    // 拉列表失败不清空已有列表：离线进来也要能看见上次的作业
                    _ui.value.copy(loading = false, error = messageOf(e), offline = isNetwork(e))
                }
            )
        }
    }

    // ------------------------------------------------------------------ 取卷
    /** 打开一份卷：已提交的直接进结果页，否则进答题页。 */
    fun open(item: StudentExamItem) {
        if (item.submitted) {
            openResult(item.assignmentId, item.title)
            return
        }
        viewModelScope.launch {
            _ui.value = _ui.value.copy(
                stage = ExamStage.ANSWERING,
                loading = true,
                error = null,
                offline = false,
                assignmentId = item.assignmentId,
                paperId = item.paperId,
                title = item.title,
                paperType = item.paperType,
                questions = emptyList(),
                answers = emptyMap(),
                index = 0,
                result = null,
                draftRestored = false
            )
            val loaded = withAuth { api.questions(item.assignmentId) }
            loaded.onFailure { e ->
                _ui.value = _ui.value.copy(
                    loading = false,
                    error = messageOf(e),
                    offline = isNetwork(e)
                )
            }
            val questions = loaded.getOrNull() ?: return@launch
            val questionsAsc = questions.sortedBy { it.seq }

            // 草稿恢复：只保留仍然存在于本卷的题号，避免卷子换版后脏答案复活
            val draft = runCatching { drafts.load(item.assignmentId) }.getOrNull()
            val validSeqs = questionsAsc.map { it.seq }.toSet()
            val restored = ExamAnswerCodec.decode(draft?.answers).filterKeys { it in validSeqs }
            clientId = draft?.clientId?.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString()

            _ui.value = _ui.value.copy(
                loading = false,
                questions = questionsAsc,
                answers = restored,
                index = firstUnansweredIndex(questionsAsc, restored),
                draftRestored = restored.isNotEmpty(),
                error = null,
                offline = false
            )
        }
    }

    /** 直接看结果（列表里已提交的卷）。 */
    fun openResult(assignmentId: Long, title: String = "") {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(
                stage = ExamStage.RESULT,
                loading = true,
                error = null,
                offline = false,
                assignmentId = assignmentId,
                title = title,
                result = null
            )
            val res = withAuth { api.result(assignmentId) }
            _ui.value = res.fold(
                onSuccess = { r ->
                    _ui.value.copy(loading = false, result = r, title = r.title.ifBlank { title })
                },
                onFailure = { e ->
                    _ui.value.copy(loading = false, error = messageOf(e), offline = isNetwork(e))
                }
            )
        }
    }

    // ------------------------------------------------------------------ 作答
    /**
     * 选择题点选：单选替换，多选切换并把字母排序后连写（`ACD`）。
     *
     * 连写格式是服务端题库/判分的既有约定（`equiv.choice_equivalent` 两者都认），
     * 这里统一成连写，避免同一份答案出现 `A,C` 与 `AC` 两种写法。
     */
    fun selectOption(seq: Int, label: String) {
        val q = _ui.value.questions.firstOrNull { it.seq == seq } ?: return
        val picked = label.trim().uppercase()
        if (picked.isBlank()) return
        val current = _ui.value.answers[seq].orEmpty()

        val next = if (!q.multi) {
            picked
        } else {
            val set = current.filter { it.isLetter() }.map { it.uppercaseChar() }.toMutableSet()
            val ch = picked.firstOrNull()
            if (ch != null && set.contains(ch)) set.remove(ch) else set.addAll(picked.toList())
            set.sorted().joinToString("")
        }
        updateAnswer(seq, next)
    }

    /** 填空/解答等文本作答。 */
    fun setAnswer(seq: Int, text: String) = updateAnswer(seq, text)

    private fun updateAnswer(seq: Int, value: String) {
        val answers = _ui.value.answers.toMutableMap()
        if (value.isBlank()) answers.remove(seq) else answers[seq] = value
        _ui.value = _ui.value.copy(answers = answers)
        persistDraft()
    }

    fun goTo(index: Int) {
        val clamped = index.coerceIn(0, (_ui.value.total - 1).coerceAtLeast(0))
        if (clamped != _ui.value.index) _ui.value = _ui.value.copy(index = clamped)
    }

    fun next() = goTo(_ui.value.index + 1)

    fun prev() = goTo(_ui.value.index - 1)

    /** 每次改动都落盘：读的是当前 state，所以连点也不会出现"旧答案覆盖新答案"。 */
    private fun persistDraft() {
        val s = _ui.value
        if (s.assignmentId == 0L) return
        val id = clientId.ifBlank { UUID.randomUUID().toString().also { clientId = it } }
        viewModelScope.launch {
            runCatching {
                drafts.save(
                    ExamDraft(
                        assignmentId = s.assignmentId,
                        paperId = s.paperId,
                        answers = ExamAnswerCodec.encode(_ui.value.answers),
                        clientId = id,
                        updatedAt = System.currentTimeMillis()
                    )
                )
            }
        }
    }

    // ------------------------------------------------------------------ 提交
    /**
     * 交卷。未作答的题按空串上交（服务端按卷内题数算总数，空即错），
     * 是否允许带空提交由 UI 二次确认决定，这里不做拦截。
     */
    fun submit() {
        val s = _ui.value
        if (s.total == 0 || s.submitting) return
        viewModelScope.launch {
            _ui.value = _ui.value.copy(submitting = true, error = null, offline = false)
            val id = clientId.ifBlank { UUID.randomUUID().toString().also { clientId = it } }
            // 先落盘再提交：万一提交过程中崩溃/断网，重试仍复用同一个 client_id
            runCatching {
                drafts.save(
                    ExamDraft(
                        assignmentId = s.assignmentId,
                        paperId = s.paperId,
                        answers = ExamAnswerCodec.encode(s.answers),
                        clientId = id,
                        updatedAt = System.currentTimeMillis()
                    )
                )
            }
            val body = ExamSubmitRequest(
                clientId = id,
                answers = s.questions.map {
                    val raw = s.answers[it.seq].orEmpty()
                    // 多空填空附带逐空数组（空串占位），服务端据此判「漏答某空」
                    ExamSubmitItem(it.seq, raw, blanks = it.blankParts(raw))
                }
            )
            val res = withAuth { api.submit(s.assignmentId, body) }
            res.fold(
                onSuccess = { r ->
                    // 交卷成功才删草稿；失败一律保留，学生回来还能接着答
                    runCatching { drafts.delete(s.assignmentId) }
                    _ui.value = _ui.value.copy(
                        submitting = false,
                        stage = ExamStage.RESULT,
                        result = r,
                        title = r.title.ifBlank { _ui.value.title },
                        error = null,
                        offline = false,
                        // 列表里的"已提交"立刻同步，不必等用户下拉刷新
                        exams = _ui.value.exams.map {
                            if (it.assignmentId == s.assignmentId) it.copy(submitted = true) else it
                        }
                    )
                },
                onFailure = { e ->
                    _ui.value = _ui.value.copy(
                        submitting = false,
                        error = messageOf(e),
                        offline = isNetwork(e)
                    )
                }
            )
        }
    }

    // ------------------------------------------------------------------ 翻页
    fun backToList() {
        _ui.value = _ui.value.copy(
            stage = ExamStage.LIST,
            error = null,
            offline = false,
            questions = emptyList(),
            answers = emptyMap(),
            index = 0,
            draftRestored = false,
            result = null
        )
    }

    /** 结果页返回时回到答题页（仅当还没交卷，理论上不会用到，留作容错）。 */
    fun backToAnswering() {
        if (_ui.value.questions.isEmpty()) {
            backToList()
            return
        }
        _ui.value = _ui.value.copy(stage = ExamStage.ANSWERING, result = null)
    }

    // ------------------------------------------------------------------ 内部
    private suspend fun <T> withAuth(block: suspend () -> T): Result<T> =
        LoginManager.withAuth(block)

    private fun isNetwork(e: Throwable): Boolean =
        e !is HttpException && e !is IllegalStateException

    private fun messageOf(e: Throwable): String = when {
        e is HttpException -> when (e.code()) {
            401 -> "登录已失效，请重新登录后再试"
            404 -> "这份作业不在可作答列表中"
            in 500..599 -> "服务暂时不可用（${e.code()}），稍后再试"
            else -> "请求失败（${e.code()}）"
        }
        // 登录失败是 IllegalStateException（见 LoginManager.withAuth），要如实给文案
        e is IllegalStateException -> e.message ?: "登录失败，请检查网络后重试"
        // 网络类异常：OkHttp/IO/超时，文案不暴露技术细节
        else -> EXAM_OFFLINE_HINT
    }

    private fun firstUnansweredIndex(questions: List<ExamQuestionView>, answers: Map<Int, String>): Int {
        val i = questions.indexOfFirst { answers[it.seq].isNullOrBlank() }
        return if (i >= 0) i else 0
    }
}
