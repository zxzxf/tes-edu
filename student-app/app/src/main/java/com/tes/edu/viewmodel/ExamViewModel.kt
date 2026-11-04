package com.tes.edu.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.local.ExamAnswerCodec
import com.tes.edu.data.local.ExamDraftStore
import com.tes.edu.data.local.ExamListCacheStore
import com.tes.edu.data.local.ExamPaperCacheStore
import com.tes.edu.data.local.RoomExamDraftStore
import com.tes.edu.data.local.RoomExamListCacheStore
import com.tes.edu.data.local.RoomExamPaperCacheStore
import com.tes.edu.data.local.ExamResultCacheStore
import com.tes.edu.data.local.RoomExamResultCacheStore
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.ExamApi
import com.tes.edu.data.remote.ExamQuestionView
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.QuestionGraphDto
import com.tes.edu.data.remote.ExamSolutionDto
import com.tes.edu.data.remote.ExamSubmitItem
import com.tes.edu.data.remote.ExamSubmitRequest
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.StudentExamItem
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import java.util.UUID

/** 单题解析的加载状态（M3-1/M3-2）。 */
data class ExamSolutionState(
    val loading: Boolean = false,
    val data: ExamSolutionDto? = null,
    val error: String? = null
) {
    /** 有内容可展示（生成失败但带原因时也算「已请求过」）。 */
    val requested: Boolean get() = data != null || error != null
}

/** M3-6 单题知识子图的加载状态（结果页按题展开，点开才拉）。 */
data class QuestionGraphState(
    val loading: Boolean = false,
    val data: QuestionGraphDto? = null,
    val error: String? = null
) {
    /** 已请求过（成功或失败都算）；失败仍允许重试，所以和 [requested] 分开判。 */
    val requested: Boolean get() = data != null || error != null
}

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
    val result: ExamResultDto? = null,
    /** M3-1 逐题解析：key=卷内题号。加载态按题存，切题/翻页不互相顶掉。 */
    val solutions: Map<Int, ExamSolutionState> = emptyMap(),
    /** M3-6 逐题知识子图：key=卷内题号，结果页展开该题时才加载。 */
    val questionGraphs: Map<Int, QuestionGraphState> = emptyMap()
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
    private val drafts: ExamDraftStore = RoomExamDraftStore(),
    private val cache: ExamPaperCacheStore = RoomExamPaperCacheStore(),
    private val listCache: ExamListCacheStore = RoomExamListCacheStore(),
    /** 判分结果缓存（结果页弱网秒开，见 [openResult]）。 */
    private val resultCache: ExamResultCacheStore = RoomExamResultCacheStore(),
    /**
     * 重试间隔（毫秒）。生产用 [RETRY_DELAY_MS]；单测传 0，
     * 否则协程会挂在 `delay` 上、断言跑在看结果之前（UnconfinedTestDispatcher
     * 不推进虚拟时间，`delay` 之后那半段根本没执行）。
     */
    private val retryDelayMs: Long = RETRY_DELAY_MS
) : ViewModel() {

    private val _ui = MutableStateFlow(ExamUiState())
    val ui: StateFlow<ExamUiState> = _ui.asStateFlow()

    /** 当前卷的幂等键，随草稿持久化；换卷时重新取。 */
    private var clientId: String = ""

    // ------------------------------------------------------------------ 列表
    /**
     * 拉作业列表。
     *
     * 两条止血措施（2026-11-04，公网入口走 Cloudflare 隧道、跨境丢包 16–25%）：
     *  1. **失败回退本地缓存**：清单落过盘就直接出（离线进来不是白屏）；
     *  2. **网络类失败自动重试一次**：丢包导致的握手/读超时重来一次多半就过了。
     *     HTTP 错误（4xx/5xx）不重试——重试也不会变好，只会让学生多等一轮。
     */
    fun refresh() {
        viewModelScope.launch {
            _ui.value = _ui.value.copy(loading = true, error = null, offline = false)
            val res = withAuthRetry { api.list() }
            res.fold(
                onSuccess = { list ->
                    runCatching { listCache.save(list) }
                    _ui.value = _ui.value.copy(
                        loading = false,
                        exams = list,
                        loaded = true,
                        error = null,
                        offline = false
                    )
                },
                onFailure = { e ->
                    // 拉列表失败不清空已有列表：离线进来也要能看见上次的作业
                    val fallback = if (_ui.value.exams.isEmpty()) {
                        runCatching { listCache.load() }.getOrNull().orEmpty()
                    } else {
                        emptyList()
                    }
                    _ui.value = _ui.value.copy(
                        loading = false,
                        // 命中缓存时把 loaded 标上：UI 才会渲染清单而不是「暂无作业」空态
                        loaded = _ui.value.loaded || fallback.isNotEmpty(),
                        exams = _ui.value.exams.ifEmpty { fallback },
                        error = messageOf(e),
                        offline = isNetwork(e)
                    )
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
            // 1) 先出缓存：命中即秒开，弱网下不再转圈（卷题干是发布时冻结的快照，可安全缓存）
            val cached = runCatching { cache.load(item.assignmentId) }.getOrNull()
            val hadCache = !cached.isNullOrEmpty()
            if (hadCache) {
                applyQuestions(item, cached!!)
            } else {
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
                    draftRestored = false,
                    // 逐题解析/图谱按卷缓存，换卷必须清空，否则 A 卷的图会挂到 B 卷同题号上
                    solutions = emptyMap(),
                    questionGraphs = emptyMap()
                )
            }

            // 2) 后台拉最新：优先走合并接口 bundle（一次拿全题干，弱网少一次隧道往返）；
            //    旧服务端无该路由回 404 → 降级到 questions()。成功覆盖缓存；题集一致则不动 UI。
            val loaded = loadQuestionsWithFallback(item.assignmentId)
            if (loaded.isSuccess) {
                val fresh = loaded.getOrNull()!!.sortedBy { it.seq }
                runCatching { cache.save(item.assignmentId, item.paperId, fresh) }
                val freshSeqs = fresh.map { it.seq }.toSet()
                // 仅当没有缓存、或卷子被修订（题号集合变了）才整体重排，否则保留当前作答与光标
                if (!hadCache || _ui.value.questions.map { it.seq }.toSet() != freshSeqs) {
                    applyQuestions(item, fresh)
                }
            } else if (!hadCache) {
                // 没缓存又拉失败 → 报错（题目都进不来，谈不上作答）
                val e = loaded.exceptionOrNull()!!
                _ui.value = _ui.value.copy(loading = false, error = messageOf(e), offline = isNetwork(e))
            }
            // 有缓存且拉失败：已展示缓存内容，静默保留，不覆盖报错
        }
    }

    /** 把题干套进当前 UI 状态（含草稿恢复、光标定位、client_id 取用）。 */
    private suspend fun applyQuestions(item: StudentExamItem, questions: List<ExamQuestionView>) {
        val questionsAsc = questions.sortedBy { it.seq }
        // 草稿恢复：只保留仍然存在于本卷的题号，避免卷子换版后脏答案复活
        val draft = runCatching { drafts.load(item.assignmentId) }.getOrNull()
        val validSeqs = questionsAsc.map { it.seq }.toSet()
        val restored = ExamAnswerCodec.decode(draft?.answers).filterKeys { it in validSeqs }
        clientId = draft?.clientId?.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString()

        _ui.value = _ui.value.copy(
            stage = ExamStage.ANSWERING,
            loading = false,
            error = null,
            offline = false,
            assignmentId = item.assignmentId,
            paperId = item.paperId,
            title = item.title,
            paperType = item.paperType,
            questions = questionsAsc,
            answers = restored,
            index = firstUnansweredIndex(questionsAsc, restored),
            draftRestored = restored.isNotEmpty(),
            result = null,
            solutions = emptyMap(),
            questionGraphs = emptyMap()
        )
    }

    /** 直接看结果（列表里已提交的卷）。 */
    fun openResult(assignmentId: Long, title: String = "") {
        viewModelScope.launch {
            // 1) 先出缓存：命中即秒开（结果除人工改判外不可变，可安全缓存）。
            //    缓存命中也先 loading=false，弱网进来不是白屏；无缓存才转圈。
            val cached = runCatching { resultCache.load(assignmentId) }.getOrNull()
            val hadCache = cached != null
            _ui.value = _ui.value.copy(
                stage = ExamStage.RESULT,
                loading = !hadCache,
                error = null,
                offline = false,
                assignmentId = assignmentId,
                title = title,
                result = cached,
                solutions = emptyMap(),
                questionGraphs = emptyMap()
            )

            // 2) 后台拉最新：优先 bundle（一次拿全），失败降级 result()。
            val res = loadResultWithFallback(assignmentId)
            _ui.value = res.fold(
                onSuccess = { r ->
                    runCatching { resultCache.save(assignmentId, r) }
                    _ui.value.copy(loading = false, result = r, title = r.title.ifBlank { title })
                },
                onFailure = { e ->
                    if (hadCache) {
                        _ui.value.copy(loading = false)
                    } else {
                        _ui.value.copy(
                            loading = false,
                            error = messageOf(e),
                            offline = isNetwork(e)
                        )
                    }
                }
            )
        }
    }

    // ------------------------------------------------------------------ 解析
    /**
     * M3-1 取本题解析（只有交卷后才允许，服务端对未提交卷返回 403）。
     *
     * 结果按题号缓存在 state 里：同一题连点不重复打接口，切到别的题再回来也不丢。
     * 失败（含未交卷）不抛异常，按题展示原因——解析是增值服务，不该让结果页崩掉。
     */
    fun loadSolution(seq: Int) {
        val s = _ui.value
        if (s.assignmentId == 0L) return
        val current = s.solutions[seq]
        if (current?.loading == true || current?.data != null) return
        _ui.value = s.copy(
            solutions = s.solutions + (seq to ExamSolutionState(loading = true))
        )
        viewModelScope.launch {
            val res = withAuth { api.solution(s.assignmentId, seq) }
            _ui.value = _ui.value.copy(
                solutions = _ui.value.solutions + (
                    seq to res.fold(
                        onSuccess = { data ->
                            ExamSolutionState(
                                data = data,
                                error = if (data.ok) null else data.reason.ifBlank {
                                    "解析暂时生成失败，可稍后重试"
                                }
                            )
                        },
                        onFailure = { e -> ExamSolutionState(error = solutionMessageOf(e)) }
                    )
                )
            )
        }
    }

    // ------------------------------------------------------------------ 逐题图谱
    /**
     * M3-6 取本题知识子图：结果页**展开该题时**才调用，不是进结果页就拉整卷。
     *
     * 与 [loadSolution] 同一套按题缓存：十道题的卷子不会一次打十个请求，同一题
     * 反复展开也不重复打接口。失败保留错误文案但允许重试（error 不当缓存），
     * 图谱是增值信息，加载失败绝不影响判分结果本身。
     */
    fun loadQuestionGraph(seq: Int) {
        val s = _ui.value
        if (s.assignmentId == 0L) return
        val current = s.questionGraphs[seq]
        if (current?.loading == true || current?.data != null) return
        _ui.value = s.copy(
            questionGraphs = s.questionGraphs + (seq to QuestionGraphState(loading = true))
        )
        viewModelScope.launch {
            val res = withAuth { api.questionGraph(s.assignmentId, seq) }
            _ui.value = _ui.value.copy(
                questionGraphs = _ui.value.questionGraphs + (
                    seq to res.fold(
                        onSuccess = { data -> QuestionGraphState(data = data) },
                        onFailure = { e -> QuestionGraphState(error = graphMessageOf(e)) }
                    )
                )
            )
        }
    }

    /** 404 = 该 seq 不在本卷（老服务端也还没有这个路由），给可行动的提示。 */
    private fun graphMessageOf(e: Throwable): String = when {
        e is HttpException && e.code() == 404 -> "这道题暂无知识图谱（卷子可能已更新）"
        else -> "知识图谱加载失败：${messageOf(e)}"
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
                    runCatching { resultCache.save(s.assignmentId, r) }
                    _ui.value = _ui.value.copy(
                        submitting = false,
                        stage = ExamStage.RESULT,
                        result = r,
                        title = r.title.ifBlank { _ui.value.title },
                        error = null,
                        offline = false,
                        solutions = emptyMap(),
                        questionGraphs = emptyMap(),
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
            result = null,
            solutions = emptyMap(),
            questionGraphs = emptyMap()
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
    /**
     * 取卷合并接口优先，失败降级到老取卷接口。
     *
     * 弱网进卷只发一个请求（bundle 一次拿全题干）；旧服务端没有该路由回 404，
     * 这里退回 [ExamApi.questions]，保证新旧服务端都能用。
     */
    private suspend fun loadQuestionsWithFallback(assignmentId: Long): Result<List<ExamQuestionView>> {
        val b = withAuthRetry { api.bundle(assignmentId) }
        if (b.isSuccess) return Result.success(b.getOrNull()!!.questions)
        return withAuthRetry { api.questions(assignmentId) }
    }

    /**
     * 结果优先走 bundle（一次拿全，含题干+结果），bundle 不可用或未带结果时降级 result()。
     */
    private suspend fun loadResultWithFallback(assignmentId: Long): Result<ExamResultDto> {
        val b = withAuthRetry { api.bundle(assignmentId) }
        if (b.isSuccess) {
            val r = b.getOrNull()!!.result
            if (r != null) return Result.success(r)
        }
        return withAuthRetry { api.result(assignmentId) }
    }

    private suspend fun <T> withAuth(block: suspend () -> T): Result<T> =
        LoginManager.withAuth(block)

    /**
     * 网络类失败自动重试一次（GET 专用，见 [refresh] 的说明）。
     *
     * 只重试 IO 类异常（连接重置 / 握手超时 / 读超时都在这类里）；HTTP 4xx/5xx
     * 与登录失败直接返回——重试不会变好，只会让学生多等一个 [RETRY_DELAY_MS]。
     */
    private suspend fun <T> withAuthRetry(block: suspend () -> T): Result<T> {
        var last: Throwable? = null
        repeat(NETWORK_ATTEMPTS) { attempt ->
            val res = withAuth(block)
            if (res.isSuccess) return res
            val e = res.exceptionOrNull()!!
            if (!isTransient(e) || attempt == NETWORK_ATTEMPTS - 1) return res
            last = e
            if (retryDelayMs > 0) delay(retryDelayMs)
        }
        return Result.failure(last ?: IOException("请求失败"))
    }

    /** 值得重试的失败：纯网络层异常（HttpException 是协议层错误，不算）。 */
    private fun isTransient(e: Throwable): Boolean = e is IOException && e !is HttpException

    /** 解析接口的错误文案：403 是「还没交卷」，要和学生说清楚而不是报「请求失败」。 */
    private fun solutionMessageOf(e: Throwable): String = when {
        e is HttpException && e.code() == 403 -> "交卷后才能查看解析"
        e is HttpException && e.code() == 404 -> "这道题不在本卷中，可能卷子已更新"
        else -> messageOf(e)
    }

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

    private companion object {
        /** 总尝试次数：初次 + 1 次重试。再多只是让学生多等，丢包是概率事件，一次足够。 */
        const val NETWORK_ATTEMPTS = 2

        /** 重试间隔：短到用户无感（还在转圈），长到足以避开一次瞬时抖动。 */
        const val RETRY_DELAY_MS = 700L
    }
}
