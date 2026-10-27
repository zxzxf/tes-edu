package com.tes.edu.viewmodel

import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.GraphResponse
import com.tes.edu.data.remote.LoginRequest
import com.tes.edu.data.remote.SimilarRequest
import com.tes.edu.data.remote.SolveRequest
import com.tes.edu.data.remote.SolveResponse
import com.tes.edu.data.remote.TokenResponse
import com.tes.edu.data.remote.VariantsResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.MultipartBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * T-C3 "直接看解析"一等功能 + Hint 阶梯对话的 App 侧 mock 走通。
 *
 * 用 fake ApiService 记录请求契约（mode/hint/student_reply 字段）并回放
 * 后端响应字段（与 agent-backend schemas.py 对齐），验证 ViewModel 状态机：
 * 开始引导 -> 回应（分步推进）-> 直接看解析（直达 L4，按钮随后隐藏）。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SolveViewModelTest {

    private class FakeApi : ApiService {
        val requests = mutableListOf<SolveRequest>()
        var next: SolveResponse = SolveResponse(answer = "")

        override suspend fun solve(req: SolveRequest): SolveResponse {
            requests += req
            return next
        }

        override suspend fun solveImage(file: MultipartBody.Part): SolveResponse = next

        override suspend fun login(req: LoginRequest): TokenResponse = error("unused")

        override suspend fun knowledgeGraph(studentId: String, subject: String?): GraphResponse =
            error("unused")

        override suspend fun similar(req: SimilarRequest): SolveResponse = next

        override suspend fun variants(kp: String, n: Int): VariantsResponse = error("unused")
    }

    private lateinit var api: FakeApi
    private lateinit var vm: SolveViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = FakeApi()
        vm = SolveViewModel(api)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun startHint_sendsHintFlag_andActivatesLadder() {
        api.next = SolveResponse(
            answer = "这道题考的是：集合运算。",
            hintLevel = 0,
            hintLevelName = "点考点",
            hintMaxRounds = 1,
            questionTier = "memory",
            questionTierName = "记忆型",
            questionType = "单选",
            hintRounds = 1
        )
        vm.startHint("集合交集选择题")
        val ui = vm.ui.value
        val req = api.requests.single()
        assertTrue("hint=true 才会进阶梯", req.hint)
        assertNull("mode 不传由后端路由", req.mode)
        assertTrue(ui.hintActive)
        assertEquals(0, ui.hintLevel)
        assertEquals("点考点", ui.hintLevelName)
        assertEquals("记忆型", ui.questionTierName)
        assertEquals(1, ui.hintMaxRounds)
        assertTrue("引导中且未看解析 -> 常驻按钮可见", ui.showDirectReveal)
        assertFalse(ui.loading)
    }

    @Test
    fun replyHint_sendsStudentReply_andAdvancesStep() {
        vm.startHint("二次函数综合题") // 首轮
        api.next = SolveResponse(
            answer = "关键依据（第 2/4 步）：判别式大于零",
            hintLevel = 0,
            hintLevelName = "点考点",
            hintFinale = true,
            hintStep = 2,
            hintStepTotal = 4,
            hintStepText = "由两交点不同可得判别式 Δ>0",
            hintRounds = 2,
            questionTier = "finale",
            questionTierName = "压轴",
            hintMaxRounds = 4
        )
        vm.replyHint("二次函数综合题", "我先联立方程")
        val ui = vm.ui.value
        val req = api.requests.last()
        assertTrue(req.hint)
        assertEquals("我先联立方程", req.studentReply)
        assertEquals(2, ui.hintStep)
        assertEquals(4, ui.hintStepTotal)
        assertTrue(ui.hintFinale)
        assertEquals("压轴", ui.questionTierName)
    }

    @Test
    fun revealDirect_sendsModeDirect_andLandsOnL4() {
        vm.startHint("二次函数综合题")
        api.next = SolveResponse(
            answer = "完整解析如下……复盘三问：这题考什么 / 卡在哪一步 / 同型题怎么识别",
            hintLevel = 4,
            hintLevelName = "完整解析+复盘",
            hintDirectReveal = true,
            hintRounds = 2,
            questionTier = "finale",
            hintMaxRounds = 4
        )
        vm.revealDirect("二次函数综合题")
        val ui = vm.ui.value
        val req = api.requests.last()
        assertEquals("direct", req.mode)
        assertEquals(4, ui.hintLevel)
        assertTrue(ui.hintDirectReveal)
        assertFalse("直达 L4 后常驻按钮隐藏", ui.showDirectReveal)
        assertFalse(ui.loading)
    }

    @Test
    fun solve_nonHintRound_carriesTierFields() {
        api.next = SolveResponse(
            answer = "参考答案",
            questionTier = "medium",
            questionTierName = "中等",
            questionType = "填空",
            hintMaxRounds = 2
        )
        vm.solve("函数定义域填空题")
        val ui = vm.ui.value
        val req = api.requests.single()
        assertFalse("非阶梯轮不带 hint", req.hint)
        assertEquals("中等", ui.questionTierName)
        assertEquals(2, ui.hintMaxRounds)
        assertFalse("非阶梯轮不进阶梯", ui.hintActive)
    }

    // -------------------------------------------------- 解题步骤展示（steps）

    @Test
    fun solve_stepsExposedOnUiState() {
        api.next = SolveResponse(
            answer = "引导档不给答案",
            steps = listOf(" 先明确已知条件 ", "由 Sn 求 an 需分 n=1 与 n≥2", "")
        )
        vm.solve("已知 Sn 求通项")
        // 空白步骤被滤掉，前后空格被 trim
        assertEquals(listOf("先明确已知条件", "由 Sn 求 an 需分 n=1 与 n≥2"), vm.ui.value.visibleSteps)
    }

    @Test
    fun filterSteps_dropsBlankAndPlaceholderOnly() {
        assertEquals(emptyList<String>(), filterSteps(emptyList()))
        assertEquals(emptyList<String>(), filterSteps(listOf("", "   ")))
        // 直给档的 4 字占位：单步过短，不值得单独成卡
        assertEquals(emptyList<String>(), filterSteps(listOf("直接计算")))
        // 两步及以上照常展示
        assertEquals(
            listOf("直接给出关键步骤", "套用通法求解"),
            filterSteps(listOf("直接给出关键步骤", "套用通法求解"))
        )
        // 单步但内容够长（引导档的第一步提示）要展示
        assertEquals(
            listOf("第一步：从「数列通项」入手，明确已知条件和所求量"),
            filterSteps(listOf("第一步：从「数列通项」入手，明确已知条件和所求量"))
        )
    }

    @Test
    fun emptyStepsMeansNoCard() {
        api.next = SolveResponse(answer = "2", steps = listOf("直接计算"))
        vm.solve("1+1等于多少")
        assertTrue("单条占位步骤不展示卡片", vm.ui.value.visibleSteps.isEmpty())
    }
}
