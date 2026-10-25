package com.tes.edu.viewmodel

import com.google.gson.Gson
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
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 触类旁通（T-S5）：ViewModel 状态机 + 请求契约 + 后端 DTO 解析。
 *
 * 用 fake ApiService 回放 agent-backend `/api/app/similar` 的响应字段
 * （与 schemas.py 的 SolveResponse 对齐），验证三态与"空白题干不发请求"。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class SimilarViewModelTest {

    private class FakeApi : ApiService {
        val requests = mutableListOf<SimilarRequest>()
        var next: SolveResponse = SolveResponse(answer = "")
        var fail: Throwable? = null

        override suspend fun similar(req: SimilarRequest): SolveResponse {
            fail?.let { throw it }
            requests += req
            return next
        }

        override suspend fun solve(req: SolveRequest): SolveResponse = error("unused")

        override suspend fun solveImage(file: MultipartBody.Part): SolveResponse = error("unused")

        override suspend fun login(req: LoginRequest): TokenResponse = error("unused")

        override suspend fun knowledgeGraph(studentId: String, subject: String?): GraphResponse =
            error("unused")

        override suspend fun variants(kp: String, n: Int): VariantsResponse = error("unused")
    }

    private lateinit var api: FakeApi
    private lateinit var vm: SimilarViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = FakeApi()
        vm = SimilarViewModel(api)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun load_success_fillsItems_andClearsLoading() {
        api.next = SolveResponse(
            answer = "",
            similar = listOf("变式一", "变式二", "变式三"),
            knowledgePoints = listOf("MA-B4-C2-S1-K03")
        )
        vm.load("已知 f(x)=x^2-2x，求最小值")
        val ui = vm.ui.value
        assertEquals(listOf("变式一", "变式二", "变式三"), ui.items)
        assertEquals(listOf("MA-B4-C2-S1-K03"), ui.knowledgePoints)
        assertEquals("已知 f(x)=x^2-2x，求最小值", ui.sourceQuestion)
        assertTrue(ui.loaded)
        assertFalse(ui.loading)
    }

    @Test
    fun load_blankQuestion_givesChineseHint_andSendsNoRequest() {
        vm.load("   ")
        val ui = vm.ui.value
        assertNotNull("空白题干要给可理解的提示", ui.error)
        assertTrue(api.requests.isEmpty())
        assertFalse(ui.loading)
        assertFalse(ui.loaded)
    }

    @Test
    fun load_failure_keepsNotLoaded_andSurfacesError() {
        api.fail = RuntimeException("timeout")
        vm.load("某道题")
        val ui = vm.ui.value
        assertNotNull(ui.error)
        assertTrue("错误信息要暴露原因", ui.error!!.contains("timeout"))
        assertFalse(ui.loading)
        assertFalse("失败不算已加载，UI 才能区分空态与错误态", ui.loaded)
        assertTrue(ui.items.isEmpty())
    }

    /**
     * 波 1 实测：ws 端点额度耗尽时后端走检索兜底，返回 200 但内容是截断片段。
     * 降级标记必须传到 UI——否则会把"相似题片段：xxx…"当成可练的题搬回输入页。
     */
    @Test
    fun load_degradedFlag_reachesUi() {
        api.next = SolveResponse(
            answer = "",
            similar = listOf("相似题片段：已知集合 \$U=…（知识点：集合的概念）"),
            similarDegraded = true
        )
        vm.load("某道题")
        val ui = vm.ui.value
        assertTrue(ui.degraded)
        assertTrue(ui.items.isNotEmpty())
    }

    @Test
    fun load_notDegraded_whenBackendGenerated() {
        api.next = SolveResponse(answer = "", similar = listOf("变式一"), similarDegraded = false)
        vm.load("某道题")
        assertFalse(vm.ui.value.degraded)
    }

    @Test
    fun load_filtersBlankItems_fromBackend() {
        api.next = SolveResponse(answer = "", similar = listOf("变式一", "  ", "", "变式二"))
        vm.load("某道题")
        assertEquals(listOf("变式一", "变式二"), vm.ui.value.items)
    }

    @Test
    fun request_trimsQuestion_andCarriesCount() {
        api.next = SolveResponse(answer = "", similar = listOf("a"))
        vm.load("  某道题  ", count = 3)
        val req = api.requests.single()
        assertEquals("某道题", req.question)
        assertEquals(3, req.count)
    }

    @Test
    fun refresh_reusesLastSourceQuestion() {
        api.next = SolveResponse(answer = "", similar = listOf("a"))
        vm.load("原题 A")
        vm.refresh()
        assertEquals(2, api.requests.size)
        assertEquals("原题 A", api.requests[1].question)
        assertEquals("原题 A", vm.ui.value.sourceQuestion)
    }

    @Test
    fun clear_resetsState() {
        api.next = SolveResponse(answer = "", similar = listOf("a"))
        vm.load("原题 A")
        vm.clear()
        val ui = vm.ui.value
        assertTrue(ui.items.isEmpty())
        assertFalse(ui.loaded)
        assertEquals("", ui.sourceQuestion)
    }

    /** DTO 契约：后端 snake_case -> App 驼峰，字段名以 schemas.py 为准。 */
    @Test
    fun dto_parsesBackendSimilarJson() {
        val json = """
            {
              "answer": "",
              "steps": [],
              "similar": ["变式一", "变式二"],
              "knowledge_points": ["MA-B4-C2-S1-K03"],
              "content_hash": "abc123",
              "mode": "similar"
            }
        """.trimIndent()
        val res = Gson().fromJson(json, SolveResponse::class.java)
        assertEquals(listOf("变式一", "变式二"), res.similar)
        assertEquals(listOf("MA-B4-C2-S1-K03"), res.knowledgePoints)
        assertEquals("abc123", res.contentHash)
        assertEquals("similar", res.mode)
    }

    /** T-A4 变式题 DTO：jsonb 字段（options/knowledge_points）必须被忽略而非解析失败。 */
    @Test
    fun dto_parsesVariantsJson_ignoringJsonbFields() {
        val json = """
            {
              "kp": "MA-B4-C2-S1-K03",
              "items": [
                {
                  "id": 12, "subject": "数学", "type": "解答", "stem": "求 f(x) 的最小值",
                  "options": "[{\"k\":\"A\",\"v\":\"1\"}]",
                  "answer": "-1", "difficulty": 3,
                  "knowledge_points": "[\"MA-B4-C2-S1-K03\"]",
                  "content_hash": "h1", "kp_hit": "MA-B4-C2-S1-K03", "tier": 0
                }
              ],
              "total": 1,
              "note": null
            }
        """.trimIndent()
        val res = Gson().fromJson(json, com.tes.edu.data.remote.VariantsResponse::class.java)
        assertEquals(1, res.total)
        assertEquals("求 f(x) 的最小值", res.items.single().stem)
        assertEquals("MA-B4-C2-S1-K03", res.items.single().kpHit)
        assertEquals(0, res.items.single().tier)
    }
}
