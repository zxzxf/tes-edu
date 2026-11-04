package com.tes.edu.viewmodel

import com.tes.edu.data.remote.ApiService
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.data.remote.GraphResponse
import com.tes.edu.data.remote.LoginRequest
import com.tes.edu.data.remote.SimilarRequest
import com.tes.edu.data.remote.SolveRequest
import com.tes.edu.data.remote.SolveResponse
import com.tes.edu.data.remote.TokenResponse
import com.tes.edu.data.remote.VariantItem
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
import java.io.IOException

/**
 * 知识图谱身份口径（2026-11-04 修复）。
 *
 * 曾经的坑：GraphViewModel 把 student_001 写死并作为 Query 传给
 * GET learning/knowledge-graph，而后端不按 JWT 覆盖 student_id（错题/背诵会覆盖），
 * 于是「作业有数据、图谱全空」——查的是另一个学生的学情。
 *
 * 这里锁住三条红线：
 *  1. 图谱请求只带登录身份，状态里也不再残留演示学号；
 *  2. 每次加载都重新读登录态（换账号后不沿用旧身份）；
 *  3. 取不到登录态时宁可报错也不发请求，绝不退化成写死身份。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GraphViewModelTest {

    private class FakeApi : ApiService {
        /** 每次 knowledgeGraph 实际发出的 student_id。 */
        val graphStudentIds = mutableListOf<String>()
        var graph: GraphResponse = GraphResponse()
        var graphError: Throwable? = null
        var variants: VariantsResponse = VariantsResponse()

        override suspend fun knowledgeGraph(studentId: String, subject: String?): GraphResponse {
            graphStudentIds += studentId
            graphError?.let { throw it }
            return graph
        }

        override suspend fun variants(kp: String, n: Int): VariantsResponse = variants
        override suspend fun solve(req: SolveRequest): SolveResponse = error("unused")
        override suspend fun solveImage(file: MultipartBody.Part): SolveResponse = error("unused")
        override suspend fun login(req: LoginRequest): TokenResponse = error("unused")
        override suspend fun similar(req: SimilarRequest): SolveResponse = error("unused")
    }

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun init_loadsWithLoggedInIdentity_neverUsesDemoStudentId() {
        val api = FakeApi()
        val vm = GraphViewModel(api) { LOGIN_ID }

        // init 会自动加载：必须一上来就是登录身份
        assertEquals(listOf(LOGIN_ID), api.graphStudentIds)
        assertEquals(LOGIN_ID, vm.ui.value.studentId)
        assertNull(vm.ui.value.error)
        assertFalse(vm.ui.value.loading)
        assertFalse(api.graphStudentIds.contains("student_001"))
    }

    @Test
    fun defaultState_hasNoHardcodedStudentId() {
        assertNull(GraphUiState().studentId)
    }

    @Test
    fun retry_reusesLoggedInIdentity() {
        val api = FakeApi()
        val vm = GraphViewModel(api) { LOGIN_ID }

        vm.load()
        vm.load()

        assertEquals(listOf(LOGIN_ID, LOGIN_ID, LOGIN_ID), api.graphStudentIds)
        assertEquals(LOGIN_ID, vm.ui.value.studentId)
        assertNull(vm.ui.value.error)
    }

    @Test
    fun identityChanged_nextLoadUsesNewIdentity() {
        var current: String? = LOGIN_ID
        val api = FakeApi()
        val vm = GraphViewModel(api) { current }

        current = "student_002@pilot01.edu"
        vm.load()

        assertEquals(listOf(LOGIN_ID, "student_002@pilot01.edu"), api.graphStudentIds)
        assertEquals("student_002@pilot01.edu", vm.ui.value.studentId)
    }

    @Test
    fun noLoginState_skipsRequestAndShowsError() {
        val api = FakeApi()
        val vm = GraphViewModel(api) { null }

        assertTrue(api.graphStudentIds.isEmpty())
        assertNull(vm.ui.value.studentId)
        assertNull(vm.ui.value.graph)
        assertFalse(vm.ui.value.loading)
        assertTrue(vm.ui.value.error?.contains("登录态未就绪") == true)
    }

    @Test
    fun graphRequestFailure_goesToErrorState() {
        val api = FakeApi().apply { graphError = IOException("offline") }
        val vm = GraphViewModel(api) { LOGIN_ID }

        assertEquals(listOf(LOGIN_ID), api.graphStudentIds)
        assertFalse(vm.ui.value.loading)
        assertTrue(vm.ui.value.error?.contains("offline") == true)
    }

    @Test
    fun practiceKp_usesInjectedApi() {
        val api = FakeApi().apply {
            variants = VariantsResponse(items = listOf(VariantItem(stem = "练一题：集合运算")))
        }
        val vm = GraphViewModel(api) { LOGIN_ID }
        val picked = mutableListOf<String>()

        vm.practiceKp(GraphNode(code = "MATH-1-1")) { picked += it }

        assertEquals(listOf("练一题：集合运算"), picked)
        assertFalse(vm.ui.value.kpLoading)
        assertNull(vm.ui.value.kpNotice)
    }

    private companion object {
        /** 与线上登录响应 user_id 一致（student@tes.edu / 租户 school_pilot_01）。 */
        const val LOGIN_ID = "student@tes.edu"
    }
}
