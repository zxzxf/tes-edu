package com.tes.edu.viewmodel

import com.google.gson.Gson
import com.tes.edu.data.local.ExamDraftStore
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.remote.ExamApi
import com.tes.edu.data.remote.ExamOption
import com.tes.edu.data.remote.ExamQuestionView
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.ExamSubmitRequest
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

        val submitted = mutableListOf<Pair<Long, ExamSubmitRequest>>()

        override suspend fun list(): List<StudentExamItem> {
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

    private lateinit var api: FakeExamApi
    private lateinit var store: FakeDraftStore
    private lateinit var vm: ExamViewModel

    private val assignmentId = 7L

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = FakeExamApi()
        store = FakeDraftStore()
        vm = ExamViewModel(api, store)
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

    /** 取卷 DTO：选项是 [{label,content}]，且**不能**有 answer 字段（后端不下发）。 */
    @Test
    fun dto_parsesQuestionJson_withoutAnswer() {
        val json = """
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
