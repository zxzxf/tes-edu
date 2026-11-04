package com.tes.edu.viewmodel

import com.google.gson.Gson
import com.tes.edu.data.local.ExamDraftStore
import com.tes.edu.data.local.ExamListCacheStore
import com.tes.edu.data.local.ExamPaperCacheStore
import com.tes.edu.data.local.ExamResultCacheStore
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.remote.ExamApi
import com.tes.edu.data.remote.ExamBundleDto
import com.tes.edu.data.remote.ExamOption
import com.tes.edu.data.remote.ExamQuestionView
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.ExamSolutionDto
import com.tes.edu.data.remote.ExamSolutionSteps
import com.tes.edu.data.remote.ExamSubmitRequest
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.data.remote.QuestionGraphDto
import com.tes.edu.data.remote.StudentExamItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

/**
 * 作业闭环学生端（M1-6）：取卷 / 结构化作答 / 草稿 / 幂等提交 / 判分结果。
 *
 * 用 fake ExamApi + 内存草稿库，锁住三条最容易写错、又最难在真机上发现的契约：
 *  1. 草稿要能恢复，且脏题号（卷子换版）不能复活；
 *  2. 多选必须连写成 `ACD`（服务端 equiv 的既有格式），单选必须替换而不是叠加；
 *  3. client_id 重试要复用——否则"幂等提交"只是自我安慰。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ExamViewModelTest {

    // ---------------------------------------------------------------- fakes
    private class FakeExamApi : ExamApi {
        var exams: List<StudentExamItem> = emptyList()
        var questions: List<ExamQuestionView> = emptyList()
        var submitResult: ExamResultDto = ExamResultDto()
        var resultDto: ExamResultDto = ExamResultDto()

        var listError: Throwable? = null
        var questionsError: Throwable? = null
        var submitError: Throwable? = null
        var resultError: Throwable? = null

        /** 合并接口 bundle：默认模拟「老服务端无此路由」回 404，让调用方走 legacy 降级；
         *  新测试可显式给 bundleDto 验证合并路径。 */
        var bundleDto: ExamBundleDto? = null
        var bundleError: Throwable? = null
        var bundleCalls = 0

        val submitted = mutableListOf<Pair<Long, ExamSubmitRequest>>()

        /** list() 调用次数：用来锁「网络失败只重试一次、HTTP 错误一次都不重试」。 */
        var listCalls = 0

        /** 前 N 次 list() 抛瞬时网络错误（模拟隧道丢包），之后正常返回。 */
        var listTransientFailures = 0

        override suspend fun list(): List<StudentExamItem> {
            listCalls++
            if (listTransientFailures > 0) {
                listTransientFailures--
                throw IOException("timeout: no recent network activity")
            }
            listError?.let { throw it }
            return exams
        }

        override suspend fun questions(assignmentId: Long): List<ExamQuestionView> {
            questionsError?.let { throw it }
            return questions
        }

        override suspend fun submit(assignmentId: Long, req: ExamSubmitRequest): ExamResultDto {
            submitted += assignmentId to req
            submitError?.let { throw it }
            return submitResult
        }

        override suspend fun result(assignmentId: Long): ExamResultDto {
            resultError?.let { throw it }
            return resultDto
        }

        override suspend fun bundle(assignmentId: Long): ExamBundleDto {
            bundleCalls++
            bundleError?.let { throw it }
            return bundleDto ?: throw retrofit2.HttpException(
                retrofit2.Response.error<Any>(
                    404,
                    "{\"detail\":\"not found\"}"
                        .toResponseBody("application/json".toMediaType())
                )
            )
        }

        var solutionDto: ExamSolutionDto = ExamSolutionDto()
        var solutionError: Throwable? = null
        val solutionCalls = mutableListOf<Pair<Long, Int>>()

        override suspend fun solution(assignmentId: Long, seq: Int): ExamSolutionDto {
            solutionCalls += assignmentId to seq
            solutionError?.let { throw it }
            return solutionDto
        }

        override suspend fun paperGraph(assignmentId: Long) =
            com.tes.edu.data.remote.PaperGraphDto(paperId = 11)

        var questionGraphDto: QuestionGraphDto = QuestionGraphDto()
        var questionGraphError: Throwable? = null
        val questionGraphCalls = mutableListOf<Pair<Long, Int>>()

        override suspend fun questionGraph(assignmentId: Long, seq: Int): QuestionGraphDto {
            questionGraphCalls += assignmentId to seq
            questionGraphError?.let { throw it }
            return questionGraphDto
        }
    }

    private class FakeDraftStore : ExamDraftStore {
        val saved = mutableMapOf<Long, ExamDraft>()

        override suspend fun load(assignmentId: Long): ExamDraft? = saved[assignmentId]

        override suspend fun save(draft: ExamDraft) {
            saved[draft.assignmentId] = draft
        }

        override suspend fun delete(assignmentId: Long) {
            saved.remove(assignmentId)
        }
    }

    /** 内存版卷题干缓存：saved 存的是"是否已写入"，questions 存实际题干。 */
    private class FakePaperCacheStore : ExamPaperCacheStore {
        val questions = mutableMapOf<Long, List<ExamQuestionView>>()

        override suspend fun load(assignmentId: Long): List<ExamQuestionView>? =
            questions[assignmentId]

        override suspend fun save(assignmentId: Long, paperId: Long, qs: List<ExamQuestionView>) {
            questions[assignmentId] = qs
        }
    }

    /** 内存版作业列表缓存：只有一份（真实现按学生 id 分行，单测只验行为）。 */
    private class FakeListCacheStore : ExamListCacheStore {
        var items: List<StudentExamItem>? = null
        var saves = 0

        override suspend fun load(): List<StudentExamItem>? = items

        override suspend fun save(items: List<StudentExamItem>) {
            this.items = items
            saves++
        }
    }

    /** 内存版判分结果缓存：results 按 assignmentId 存，验证弱网秒开/回退逻辑。 */
    private class FakeExamResultCacheStore : ExamResultCacheStore {
        val results = mutableMapOf<Long, ExamResultDto>()

        override suspend fun load(assignmentId: Long): ExamResultDto? = results[assignmentId]

        override suspend fun save(assignmentId: Long, result: ExamResultDto) {
            results[assignmentId] = result
        }
    }

    private lateinit var api: FakeExamApi
    private lateinit var store: FakeDraftStore
    private lateinit var cache: FakePaperCacheStore
    private lateinit var listCache: FakeListCacheStore
    private lateinit var resultCache: FakeExamResultCacheStore
    private lateinit var vm: ExamViewModel

    private val assignmentId = 7L

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = FakeExamApi()
        store = FakeDraftStore()
        cache = FakePaperCacheStore()
        listCache = FakeListCacheStore()
        resultCache = FakeExamResultCacheStore()
        vm = ExamViewModel(api, store, cache, listCache, resultCache, retryDelayMs = 0L)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    // ---------------------------------------------------------------- helpers
    private fun choice(seq: Int, multi: Boolean = false) = ExamQuestionView(
        seq = seq,
        qtype = "choice",
        multi = multi,
        stem = "第 $seq 题题干",
        options = listOf(
            ExamOption("A", "1"), ExamOption("B", "2"),
            ExamOption("C", "3"), ExamOption("D", "4")
        )
    )

    private fun blank(seq: Int) = ExamQuestionView(seq = seq, qtype = "blank", stem = "填 $seq")

    private fun subjective(seq: Int) =
        ExamQuestionView(seq = seq, qtype = "subjective", stem = "解 $seq")

    private fun item(submitted: Boolean = false) = StudentExamItem(
        assignmentId = assignmentId,
        paperId = 11,
        title = "高三·数学·混合卷（3题）",
        paperType = "mixed",
        grade = "高三",
        subject = "数学",
        questionCount = 3,
        dueAt = "2026-11-02T15:59:59+08:00",
        submitted = submitted
    )

    /** 打开一份 3 题卷（单选/多选/填空）。 */
    private fun openMixed() {
        api.questions = listOf(choice(1), choice(2, multi = true), blank(3))
        vm.refresh()
        vm.open(item())
    }

    // ---------------------------------------------------------------- 列表
    @Test
    fun refresh_success_fillsList_andMarksLoaded() {
        api.exams = listOf(item())
        vm.refresh()
        val ui = vm.ui.value
        assertEquals(1, ui.exams.size)
        assertTrue(ui.loaded)
        assertFalse(ui.loading)
        assertNull(ui.error)
        assertEquals("纯选择题", StudentExamItem(1, 1, paperType = "pure_choice").paperTypeLabel)
        assertEquals("混合卷", ui.exams.single().paperTypeLabel)
    }

    @Test
    fun refresh_failure_keepsExistingList_andFlagsOffline() {
        api.exams = listOf(item())
        vm.refresh()
        api.listError = IOException("timeout")
        vm.refresh()
        val ui = vm.ui.value
        assertEquals("断网不该清空已有作业", 1, ui.exams.size)
        assertTrue(ui.offline)
        assertEquals(EXAM_OFFLINE_HINT, ui.error)
        assertFalse(ui.loading)
    }

    // ------------------------------------------------- 止血：列表缓存 + 一次重试
    @Test
    fun refresh_success_writesListCache() {
        api.exams = listOf(item())
        vm.refresh()
        assertEquals("拉列表成功要落盘，下次断网才有东西可回退", 1, listCache.saves)
        assertEquals(1, listCache.items?.size)
    }

    @Test
    fun refresh_transientNetworkFailure_retriesOnce_thenSucceeds() {
        api.exams = listOf(item())
        api.listTransientFailures = 1  // 第一次模拟隧道丢包超时
        vm.refresh()
        val ui = vm.ui.value
        assertEquals("瞬时网络错误应自动重试一次", 2, api.listCalls)
        assertEquals(1, ui.exams.size)
        assertFalse("重试成功不该报离线", ui.offline)
        assertNull(ui.error)
    }

    @Test
    fun refresh_httpError_isNotRetried() {
        api.listError = httpError(500)
        vm.refresh()
        assertEquals("HTTP 500 重试不会变好，不该让学生多等一轮", 1, api.listCalls)
        assertTrue(vm.ui.value.error!!.contains("500"))
    }

    @Test
    fun refresh_networkFailure_fallsBackToCachedList() {
        listCache.items = listOf(item(submitted = true))
        api.listError = IOException("timeout")
        vm.refresh()
        val ui = vm.ui.value
        assertEquals("断网也要能看见上次的作业清单", 1, ui.exams.size)
        assertTrue(ui.exams.single().submitted)
        assertTrue("命中缓存时 loaded 必须为 true，否则 UI 走「暂无作业」空态", ui.loaded)
        assertTrue(ui.offline)
    }

    @Test
    fun refresh_failure_withEmptyCache_staysUnloaded() {
        api.listError = IOException("timeout")
        vm.refresh()
        val ui = vm.ui.value
        assertTrue(ui.exams.isEmpty())
        assertFalse("没有任何缓存时不要谎报已加载", ui.loaded)
        assertTrue(ui.offline)
    }

    // ---------------------------------------------------------------- 取卷
    @Test
    fun open_loadsQuestions_andStartsAtFirstUnanswered() {
        openMixed()
        val ui = vm.ui.value
        assertEquals(ExamStage.ANSWERING, ui.stage)
        assertEquals(3, ui.total)
        assertEquals(0, ui.index)
        assertEquals(assignmentId, ui.assignmentId)
        assertFalse(ui.loading)
        assertFalse("没有草稿时不该谎报已恢复", ui.draftRestored)
    }

    @Test
    fun open_restoresDraft_andJumpsToFirstGap() {
        // 上次答了第 1 题，第 2 题空着
        store.saved[assignmentId] = ExamDraft(
            assignmentId = assignmentId, paperId = 11,
            answers = """{"1":"B"}""", clientId = "c-fixed", updatedAt = 1
        )
        openMixed()
        val ui = vm.ui.value
        assertEquals("B", ui.answers[1])
        assertEquals("恢复后光标停在第 2 题", 1, ui.index)
        assertTrue(ui.draftRestored)
        assertEquals(1, ui.answeredCount)
    }

    @Test
    fun open_dropsDraftAnswersForSeqsNotInPaper() {
        store.saved[assignmentId] = ExamDraft(
            assignmentId = assignmentId, paperId = 11,
            answers = """{"1":"A","99":"D"}""", clientId = "c-fixed", updatedAt = 1
        )
        openMixed()
        val ui = vm.ui.value
        assertEquals("卷子换版后脏题号不能复活", setOf(1), ui.answers.keys)
    }

    @Test
    fun open_submittedItem_goesStraightToResult() {
        api.resultDto = ExamResultDto(
            submissionId = 5, assignmentId = assignmentId, paperId = 11,
            title = "高三·数学·混合卷（3题）", correctCount = 2, totalCount = 3,
            autoGradeableCount = 2, manualPendingCount = 1, status = "manual_pending"
        )
        vm.open(item(submitted = true))
        val ui = vm.ui.value
        assertEquals(ExamStage.RESULT, ui.stage)
        assertNotNull(ui.result)
        assertEquals(1, ui.result!!.manualPendingCount)
        assertTrue(ui.result!!.hasManualPending)
        assertEquals("2 / 2", ui.result!!.autoScoreText)
    }

    @Test
    fun open_failure_surfacesErrorAndStaysOnAnswering() {
        api.questionsError = IOException("boom")
        vm.open(item())
        val ui = vm.ui.value
        assertEquals(ExamStage.ANSWERING, ui.stage)
        assertEquals(EXAM_OFFLINE_HINT, ui.error)
        assertEquals(0, ui.total)
    }

    // ---------------------------------------------------------------- M3-1 解析
    @Test
    fun loadSolution_cachesPerSeq_andDoesNotRefetch() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11, totalCount = 3)
        vm.open(item(submitted = true))
        api.solutionDto = ExamSolutionDto(
            paperId = 11, questionId = 3, cached = false,
            solutionSteps = ExamSolutionSteps(answer = "2", steps = listOf("移项", "开方"))
        )
        vm.loadSolution(1)
        val state = vm.ui.value.solutions[1]
        assertEquals(2, state!!.data!!.solutionSteps!!.steps.size)
        assertNull(state.error)
        // 再点同一题不应重复请求（防连点烧 token）
        vm.loadSolution(1)
        assertEquals(1, api.solutionCalls.size)
        assertEquals(assignmentId to 1, api.solutionCalls.single())
    }

    @Test
    fun loadSolution_failure_surfacesReasonButKeepsOthersUsable() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11, totalCount = 3)
        vm.open(item(submitted = true))
        api.solutionError = retrofit2.HttpException(
            retrofit2.Response.error<Any>(
                403,
                "{\"detail\":{\"code\":\"SOLUTION_LOCKED\"}}"
                    .toResponseBody("application/json".toMediaType())
            )
        )
        vm.loadSolution(2)
        val state = vm.ui.value.solutions[2]!!
        assertEquals("交卷后才能查看解析", state.error)
        assertTrue("失败不该清掉其他题的状态", vm.ui.value.solutions.keys.contains(2))
    }

    @Test
    fun loadSolution_okFalse_reportsBackendReason() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11, totalCount = 3)
        vm.open(item(submitted = true))
        api.solutionDto = ExamSolutionDto(
            paperId = 11, questionId = 1, ok = false, reason = "解析结论与卷面标答不一致，未缓存"
        )
        vm.loadSolution(1)
        assertEquals("解析结论与卷面标答不一致，未缓存", vm.ui.value.solutions[1]!!.error)
    }

    // ------------------------------------------------------------ M3-6 逐题图谱
    @Test
    fun loadQuestionGraph_lazyLoadsPerSeq_andDoesNotRefetch() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11, totalCount = 3)
        vm.open(item(submitted = true))
        api.questionGraphDto = QuestionGraphDto(
            assignmentId = assignmentId, paperId = 11, seq = 1,
            coreKps = listOf("K01"),
            nodes = listOf(GraphNode(code = "K01", title = "集合", band = "strong"))
        )
        vm.loadQuestionGraph(1)
        val state = vm.ui.value.questionGraphs[1]!!
        val dto = state.data!!
        assertEquals(1, dto.coreNodes().size)
        assertEquals("集合", dto.coreNodes().single().title)
        assertNull(state.error)
        // 十道题的卷子不该一次拉十个：没展开的题一个请求都没有
        assertEquals(1, api.questionGraphCalls.size)
        vm.loadQuestionGraph(1)
        assertEquals("同题反复展开不重复打接口", 1, api.questionGraphCalls.size)
        assertEquals(assignmentId to 1, api.questionGraphCalls.single())
    }

    @Test
    fun loadQuestionGraph_failure_isRetryable() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11)
        vm.open(item(submitted = true))
        api.questionGraphError = IOException("timeout")
        vm.loadQuestionGraph(2)
        assertTrue(vm.ui.value.questionGraphs[2]!!.error!!.contains("加载失败"))

        api.questionGraphError = null
        api.questionGraphDto = QuestionGraphDto(
            seq = 2, coreKps = listOf("K02"), nodes = listOf(GraphNode(code = "K02"))
        )
        vm.loadQuestionGraph(2)
        assertEquals("失败不能当缓存，点重试要真的再发一次", 2, api.questionGraphCalls.size)
        assertNotNull(vm.ui.value.questionGraphs[2]!!.data)
        assertNull(vm.ui.value.questionGraphs[2]!!.error)
    }

    @Test
    fun openResult_clearsQuestionGraphsOfPreviousPaper() {
        api.resultDto = ExamResultDto(assignmentId = assignmentId, paperId = 11)
        vm.open(item(submitted = true))
        api.questionGraphDto = QuestionGraphDto(
            seq = 1, coreKps = listOf("A"), nodes = listOf(GraphNode(code = "A"))
        )
        vm.loadQuestionGraph(1)
        assertNotNull(vm.ui.value.questionGraphs[1]!!.data)

        api.resultDto = ExamResultDto(assignmentId = 8L, paperId = 12)
        vm.openResult(8L)
        assertTrue("换卷必须清空，否则 A 卷的图会挂到 B 卷同题号上",
            vm.ui.value.questionGraphs.isEmpty())
        vm.loadQuestionGraph(1)
        assertEquals(listOf(assignmentId to 1, 8L to 1), api.questionGraphCalls)
    }

    // ------------------------------------------------- 结果缓存（弱网秒开 + 合并接口）
    @Test
    fun openResult_cacheHit_showsInstantly_andToleratesResultFailure() {
        // 缓存里已有结果，网络即便拉不到也该秒开、且不报联网错误
        val cached = ExamResultDto(
            submissionId = 9, assignmentId = assignmentId, paperId = 11,
            title = "高三·数学·混合卷（3题）", correctCount = 2, totalCount = 3
        )
        resultCache.results[assignmentId] = cached
        api.resultError = IOException("offline")
        vm.openResult(assignmentId)
        val ui = vm.ui.value
        // 命中缓存即秒开：不转圈、不报错、结果保留为缓存内容（而非被网络失败清掉）
        assertFalse("命中缓存不该再转圈", ui.loading)
        assertNull("有缓存时网络失败要静默保留，不报错", ui.error)
        assertEquals(cached, ui.result)
    }

    @Test
    fun openResult_cacheMiss_fetchesViaBundle_andWritesCache() {
        // 新服务端有 bundle：一次拿全结果并落盘，下次再开就能秒开
        api.bundleDto = ExamBundleDto(
            assignmentId = assignmentId, paperId = 11,
            title = "高三·数学·混合卷（3题）",
            submitted = true, questions = emptyList(),
            result = ExamResultDto(
                submissionId = 9, assignmentId = assignmentId, paperId = 11,
                title = "高三·数学·混合卷（3题）", correctCount = 3, totalCount = 3
            )
        )
        vm.openResult(assignmentId)
        val ui = vm.ui.value
        assertFalse(ui.loading)
        assertEquals(1, api.bundleCalls)
        assertNotNull(ui.result)
        assertEquals(3, ui.result!!.correctCount)
        assertEquals("取到后必须写回缓存", 3, resultCache.results[assignmentId]?.correctCount)
    }

    @Test
    fun openResult_cacheMiss_legacyFallback_whenBundle404() {
        // 老服务端没有 bundle（404）→ 降级到 result()，结果仍要落缓存
        api.resultDto = ExamResultDto(
            submissionId = 5, assignmentId = assignmentId, paperId = 11,
            title = "高三·数学·混合卷（3题）", correctCount = 1, totalCount = 3
        )
        vm.openResult(assignmentId)
        val ui = vm.ui.value
        assertEquals("404 要走 legacy result()", 1, ui.result!!.correctCount)
        assertEquals(1, resultCache.results[assignmentId]?.correctCount)
    }

    @Test
    fun submit_savesResultCache() {
        openMixed()
        api.submitResult = ExamResultDto(submissionId = 1, correctCount = 2, totalCount = 3)
        vm.submit()
        assertEquals(
            "交卷成功必须把结果落盘缓存，下次看结果才秒开",
            2, resultCache.results[assignmentId]?.correctCount
        )
    }

    // ---------------------------------------------------------------- 缓存（弱网秒开）
    @Test
    fun open_cacheHit_showsInstantlyAndToleratesNetworkFailure() {
        // 缓存里已有这道题，网络即便拉不到也该秒开、且不报联网错误
        cache.questions[assignmentId] = listOf(choice(1), choice(2), blank(3))
        api.questionsError = IOException("offline")
        vm.open(item())
        val ui = vm.ui.value
        assertEquals(3, ui.total)
        assertFalse("命中缓存不该再转圈", ui.loading)
        assertNull("有缓存时网络失败要静默保留，不报错", ui.error)
    }

    @Test
    fun open_cacheMiss_fetchesNetworkAndWritesCache() {
        api.questions = listOf(choice(1), choice(2, multi = true), blank(3))
        vm.open(item())
        // 网络取到后必须写回缓存，下次再开就能秒开
        assertEquals(3, cache.questions[assignmentId]?.size)
        assertEquals(3, vm.ui.value.total)
    }

    @Test
    fun open_cacheHit_thenRevisedPaper_reflectsNewSeqs() {
        // 缓存是旧版 3 题，服务端已修订成 4 题 → 题集变了，UI 必须重排到新版本
        cache.questions[assignmentId] = listOf(choice(1), choice(2), blank(3))
        api.questions = listOf(choice(1), choice(2), blank(3), subjective(4))
        vm.open(item())
        assertEquals("卷被修订后题集要跟随服务端", 4, vm.ui.value.total)
    }

    @Test
    fun open_cacheHit_sameSeqs_doesNotClobberInProgressState() {
        // 缓存命中且题集一致：即使后台网络返回同样内容，也不该重置用户已选/光标
        cache.questions[assignmentId] = listOf(choice(1), choice(2), blank(3))
        api.questions = listOf(choice(1), choice(2), blank(3))
        vm.open(item())
        vm.selectOption(1, "A")
        assertEquals("A", vm.ui.value.answers[1])
        // 再次 open 模拟"从列表回来"——命中缓存、题集一致，已选答案必须保留
        vm.open(item())
        assertEquals("缓存命中重开不能吞掉已选答案", "A", vm.ui.value.answers[1])
    }

    // ---------------------------------------------------------------- 作答
    @Test
    fun selectOption_single_replacesInsteadOfStacking() {
        openMixed()
        vm.selectOption(1, "A")
        assertEquals("A", vm.ui.value.answers[1])
        vm.selectOption(1, "B")
        assertEquals("单选必须替换", "B", vm.ui.value.answers[1])
    }

    @Test
    fun selectOption_multi_togglesAndSortsConcatenated() {
        openMixed()
        vm.selectOption(2, "C")
        assertEquals("C", vm.ui.value.answers[2])
        vm.selectOption(2, "A")
        assertEquals("多选要按字母序连写", "AC", vm.ui.value.answers[2])
        vm.selectOption(2, "D")
        assertEquals("ACD", vm.ui.value.answers[2])
        vm.selectOption(2, "A") // 再点一次取消
        assertEquals("CD", vm.ui.value.answers[2])
    }

    @Test
    fun setAnswer_trimsBlankToRemoved() {
        openMixed()
        vm.setAnswer(3, "3x+4y-5=0")
        assertEquals("3x+4y-5=0", vm.ui.value.answers[3])
        vm.setAnswer(3, "   ")
        assertNull("空白作答不算已答", vm.ui.value.answers[3])
        assertEquals(0, vm.ui.value.answeredCount)
    }

    @Test
    fun answer_isPersistedToDraftImmediately() {
        openMixed()
        vm.selectOption(1, "A")
        val draft = store.saved[assignmentId]
        assertNotNull("每次作答都要落草稿（断网才不丢）", draft)
        assertEquals("""{"1":"A"}""", draft!!.answers)
        assertTrue("草稿要带幂等键", draft.clientId.isNotBlank())
    }

    @Test
    fun goTo_clampsIndexIntoRange() {
        openMixed()
        vm.goTo(99)
        assertEquals(2, vm.ui.value.index)
        vm.goTo(-5)
        assertEquals(0, vm.ui.value.index)
    }

    // ---------------------------------------------------------------- 提交
    @Test
    fun submit_sendsAllSequences_withBlankForUnanswered() {
        openMixed()
        vm.selectOption(1, "A")
        vm.selectOption(2, "C")
        api.submitResult = ExamResultDto(submissionId = 1, correctCount = 2, totalCount = 3)
        vm.submit()

        val (aid, req) = api.submitted.single()
        assertEquals(assignmentId, aid)
        assertEquals(listOf(1, 2, 3), req.answers.map { it.seq })
        assertEquals(listOf("A", "C", ""), req.answers.map { it.studentAnswer })
    }

    @Test
    fun submit_reusesClientIdFromDraft_acrossRetries() {
        store.saved[assignmentId] = ExamDraft(
            assignmentId = assignmentId, paperId = 11,
            answers = """{"1":"A"}""", clientId = "c-fixed", updatedAt = 1
        )
        openMixed()
        api.submitResult = ExamResultDto(submissionId = 1)
        vm.submit()
        assertEquals("重试必须复用草稿里的幂等键", "c-fixed", api.submitted.single().second.clientId)
    }

    @Test
    fun submit_generatesClientIdOnce_andKeepsItOnFailure() {
        openMixed()
        vm.selectOption(1, "A")
        api.submitError = IOException("timeout")
        vm.submit()
        val first = api.submitted.last().second.clientId
        assertTrue(first.isNotBlank())

        api.submitError = null
        api.submitResult = ExamResultDto(submissionId = 2)
        vm.submit()
        val second = api.submitted.last().second.clientId
        assertEquals("失败重试不能换幂等键", first, second)
    }

    @Test
    fun submit_success_clearsDraft_andShowsResult() {
        api.exams = listOf(item())
        openMixed()
        vm.selectOption(1, "A")
        assertNotNull(store.saved[assignmentId])

        api.submitResult = ExamResultDto(
            submissionId = 3, assignmentId = assignmentId, paperId = 11,
            title = "高三·数学·混合卷（3题）", correctCount = 1, totalCount = 3,
            autoGradeableCount = 2, manualPendingCount = 1, status = "manual_pending"
        )
        vm.submit()

        val ui = vm.ui.value
        assertEquals(ExamStage.RESULT, ui.stage)
        assertEquals(1, ui.result!!.correctCount)
        assertNull("交卷成功要把草稿删掉", store.saved[assignmentId])
        assertFalse(ui.submitting)
        // 列表里的"已提交"要立刻同步，否则返回列表还是"开始作答"
        assertTrue(ui.exams.single().submitted)
    }

    @Test
    fun submit_failure_keepsDraft_andExplainsOffline() {
        openMixed()
        vm.selectOption(1, "A")
        api.submitError = IOException("network")
        vm.submit()

        val ui = vm.ui.value
        assertEquals("失败不能跳结果页", ExamStage.ANSWERING, ui.stage)
        assertEquals(EXAM_OFFLINE_HINT, ui.error)
        assertTrue(ui.offline)
        assertFalse(ui.submitting)
        assertNotNull("失败必须保留草稿", store.saved[assignmentId])
        assertEquals("A", ExamDataAnswers.of(store.saved[assignmentId]!!)[1])
    }

    @Test
    fun submit_httpError_reportsCodeNotOffline() {
        openMixed()
        api.submitError = httpError(409)
        vm.submit()
        val ui = vm.ui.value
        assertFalse(ui.offline)
        assertTrue(ui.error!!.contains("409"))
    }

    @Test
    fun submit_withoutQuestions_doesNothing() {
        vm.open(item())  // 没配 questions，取不到题
        api.questions = emptyList()
        vm.submit()
        assertTrue(api.submitted.isEmpty())
    }

    // ---------------------------------------------------------------- 回退
    @Test
    fun backToList_resetsAnswerStateAndKeepsExams() {
        api.exams = listOf(item())
        openMixed()
        vm.selectOption(1, "A")
        vm.backToList()
        val ui = vm.ui.value
        assertEquals(ExamStage.LIST, ui.stage)
        assertEquals(0, ui.total)
        assertTrue(ui.answers.isEmpty())
        assertEquals(1, ui.exams.size)
        assertNotNull("回列表不能顺手删草稿", store.saved[assignmentId])
    }

    // ---------------------------------------------------------------- 契约
    /** 结果 DTO：字段名以 web-backend schemas.py 为准（snake_case）。 */
    @Test
    fun dto_parsesResultJson() {
        val json = """
            {
              "submission_id": 12, "assignment_id": 7, "paper_id": 11,
              "title": "高三·数学·混合卷（3题）",
              "score": 2.0, "correct_count": 2, "total_count": 3,
              "auto_gradeable_count": 2, "manual_pending_count": 1,
              "status": "manual_pending",
              "submitted_at": "2026-10-29T15:00:00+08:00",
              "items": [
                {"seq":1,"qtype":"choice","multi":false,"student_answer":"A",
                 "correct_answer":"A","correct":true,"partial":false,
                 "status":"auto","analysis":"移项即可"},
                {"seq":2,"qtype":"choice","multi":true,"student_answer":"AC",
                 "correct_answer":"ACD","correct":false,"partial":true,
                 "status":"auto","analysis":null},
                {"seq":3,"qtype":"blank","multi":false,"student_answer":"1",
                 "correct_answer":null,"correct":false,"partial":false,
                 "status":"manual_pending","analysis":null}
              ]
            }
        """.trimIndent()
        val r = Gson().fromJson(json, ExamResultDto::class.java)
        assertEquals(2, r.correctCount)
        assertEquals(1, r.manualPendingCount)
        assertEquals("2 / 2", r.autoScoreText)
        assertEquals(3, r.items.size)
        assertEquals("正确", r.items[0].verdictLabel)
        assertEquals("漏选", r.items[1].verdictLabel)
        assertEquals("待人工批改", r.items[2].verdictLabel)
        assertTrue(r.items[2].manualPending)
        assertFalse(r.items[0].manualPending)
    }

    /**
     * 老师批改后的结果：`manual_reviewed` 要与 `auto` / `manual_pending` 区分开。
     *
     * 漏了这个状态，学生在 App 上就看不到「这题是老师判的」和评语——
     * 成绩变了却不知道为什么，等于改判没闭环。
     */
    @Test
    fun dto_parsesReviewedResult() {
        val json = """
            {
              "submission_id": 13, "assignment_id": 7, "paper_id": 11,
              "title": "高三·数学·混合卷（2题）",
              "score": 1.0, "correct_count": 1, "total_count": 2,
              "auto_gradeable_count": 2, "manual_pending_count": 0,
              "manual_reviewed_count": 2, "status": "graded",
              "items": [
                {"seq":3,"qtype":"blank","student_answer":"y轴","correct_answer":"x=0",
                 "correct":true,"partial":false,"status":"manual_reviewed",
                 "reason":"老师已复核判定","analysis":null,
                 "teacher_comment":"写「y轴」等价于 x=0，算对"},
                {"seq":4,"qtype":"subjective","student_answer":"配方得 (x+1/2)^2+3/4>0",
                 "correct_answer":"配方","correct":false,"partial":false,
                 "status":"manual_reviewed","teacher_comment":"证明不完整，缺恒成立结论"}
              ]
            }
        """.trimIndent()
        val r = Gson().fromJson(json, ExamResultDto::class.java)
        assertEquals(2, r.manualReviewedCount)
        assertEquals(0, r.manualPendingCount)
        assertFalse(r.hasManualPending)
        assertTrue(r.items[0].reviewed)
        assertFalse(r.items[0].manualPending)
        assertEquals("写「y轴」等价于 x=0，算对", r.items[0].teacherComment)
        assertEquals("正确", r.items[0].verdictLabel)
        assertEquals("错误", r.items[1].verdictLabel)
    }

    /** 取卷 DTO：选项是 [{label,content}]，且**不能**有 answer 字段（后端不下发）。 */
    @Test
    fun dto_parsesQuestionJson_withoutAnswer() {        val json = """
            [{"seq":1,"qtype":"choice","multi":false,"stem":"已知集合 A={1,2}",
              "options":[{"label":"A","content":"1"},{"label":"B","content":"2"}],
              "difficulty":3,"knowledge_points":["MA-B4-C1-S2-K02"]}]
        """.trimIndent()
        val qs = Gson().fromJson(
            json,
            com.google.gson.reflect.TypeToken.getParameterized(
                List::class.java, ExamQuestionView::class.java
            ).type
        ) as List<ExamQuestionView>
        val q = qs.single()
        assertEquals(2, q.options.size)
        assertEquals("1", q.options.first().content)
        assertEquals("单选", q.qtypeLabel)
        assertTrue(q.isChoice)
    }

    // ---------------------------------------------------------------- 多空提交（M2-3）

    /** 多空填空逐空下发，且**空位补齐**：学生漏答某空要判错，不能变成"待人工"。 */
    @Test
    fun submit_multiBlank_sendsBlanksWithEmptyPlaceholders() {
        api.exams = listOf(item())
        api.questions = listOf(
            ExamQuestionView(seq = 1, qtype = "blank", stem = "两空", blankCount = 2),
            ExamQuestionView(seq = 2, qtype = "blank", stem = "一空", blankCount = 1)
        )
        vm.refresh()
        vm.open(item())
        vm.setAnswer(1, "5；")      // 只填第 1 空，第 2 空留空
        vm.setAnswer(2, "[2,3)")
        vm.submit()

        val req = api.submitted.single().second
        val q1 = req.answers.single { it.seq == 1 }
        assertEquals(listOf("5", ""), q1.blanks)
        val q2 = req.answers.single { it.seq == 2 }
        assertNull("单空题不传 blanks，交服务端括号感知切分", q2.blanks)
    }

    /** 老版本单串作答（用逗号分隔）不在这里切——`[2,3)` 的逗号会被切坏。 */
    @Test
    fun submit_legacySingleString_leavesBlanksNull() {
        api.exams = listOf(item())
        api.questions = listOf(
            ExamQuestionView(seq = 1, qtype = "blank", stem = "两空", blankCount = 2)
        )
        vm.refresh()
        vm.open(item())
        vm.setAnswer(1, "[2,3),[4,5)")
        vm.submit()

        assertNull(api.submitted.single().second.answers.single().blanks)
    }

    /** 卷型枚举 -> 中文标签：服务端三值都要有对应文案。 */
    @Test
    fun dto_paperTypeLabels() {
        assertEquals("纯选择题", StudentExamItem(1, 1, paperType = "pure_choice").paperTypeLabel)
        assertEquals("纯填空题", StudentExamItem(1, 1, paperType = "pure_blank").paperTypeLabel)
        assertEquals("混合卷", StudentExamItem(1, 1, paperType = "mixed").paperTypeLabel)
    }

    private fun httpError(code: Int): Throwable = retrofit2.HttpException(
        retrofit2.Response.error<Any>(
            code,
            "err".toResponseBody("text/plain".toMediaType())
        )
    )

    /** 草稿 answers 解码的小工具（避免测试里到处写 Gson）。 */
    private object ExamDataAnswers {
        fun of(draft: ExamDraft): Map<Int, String> =
            com.tes.edu.data.local.ExamAnswerCodec.decode(draft.answers)
    }
}
