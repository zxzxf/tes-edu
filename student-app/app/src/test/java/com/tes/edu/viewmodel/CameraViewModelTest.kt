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
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 拍照识题状态机（T-P6）。
 *
 * 关键设计：[CameraViewModel] 把压缩当 `suspend (ByteArray) -> ByteArray` 注入，
 * 所以这里传 identity，整条链路（准备 → 上传 → 识别 → 结果/降级）能在 **JVM 上**
 * 跑通，不碰 Android `BitmapFactory`（它在单测里是 "not mocked" 桩，会直接抛异常）。
 *
 * 覆盖重点不是"能不能调通"，而是**三种不可用的表现是否区分明确**：
 * 未启用视觉模型 / 没识别出文字 / 网络失败，给学生的动作完全不同。
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CameraViewModelTest {

    private class FakeApi : ApiService {
        /** 记录真正上传的字节——用于验证"预览的那份 == 上传的那份"。 */
        var uploadedBytes: ByteArray? = null
        var next: SolveResponse = SolveResponse(answer = "")
        var fail: Throwable? = null

        override suspend fun solveImage(file: MultipartBody.Part): SolveResponse {
            val buf = Buffer()
            file.body.writeTo(buf)
            uploadedBytes = buf.readByteArray()
            fail?.let { throw it }
            return next
        }

        override suspend fun solve(req: SolveRequest): SolveResponse = error("unused")
        override suspend fun login(req: LoginRequest): TokenResponse = error("unused")
        override suspend fun knowledgeGraph(studentId: String, subject: String?): GraphResponse =
            error("unused")
        override suspend fun similar(req: SimilarRequest): SolveResponse = error("unused")
        override suspend fun variants(kp: String, n: Int): VariantsResponse = error("unused")
    }

    private lateinit var api: FakeApi
    private lateinit var vm: CameraViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        api = FakeApi()
        // compress = identity：绕开 Android BitmapFactory
        vm = CameraViewModel(api = api, compress = { it })
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun photo(): ByteArray = byteArrayOf(0x11, 0x22, 0x33, 0x44)

    @Test
    fun prepare_storesPreview_andStaysIdle() {
        vm.prepare(photo())

        val ui = vm.ui.value
        assertEquals(CameraStage.Idle, ui.stage)
        assertNull(ui.error)
        // 单测注入的是 identity 压缩，预览字节即原字节
        assertTrue(ui.preview.contentEquals(photo()))
        assertNull(api.uploadedBytes) // 只是预览，还没上传
    }

    @Test
    fun recognize_success_movesToRecognized_withTextAndKps() {
        api.next = SolveResponse(
            answer = "x=2 或 3",
            recognizedText = "解方程 x^2 - 5x + 6 = 0",
            knowledgePoints = listOf("一元二次方程"),
            vlmEnabled = true,
            degraded = false
        )
        vm.prepare(photo())
        vm.recognize()

        val ui = vm.ui.value
        assertEquals(CameraStage.Recognized, ui.stage)
        assertEquals("解方程 x^2 - 5x + 6 = 0", ui.recognizedText)
        assertEquals(listOf("一元二次方程"), ui.knowledgePoints)
        assertNull(ui.errorKind)
    }

    /** "预览的那份 == 上传的那份"：避免压缩后预览 A、上传 B 的割裂。 */
    @Test
    fun recognize_uploadsExactlyThePreviewedBytes() {
        api.next = SolveResponse(answer = "", recognizedText = "题干", vlmEnabled = true)
        vm.prepare(photo())
        vm.recognize()

        assertTrue(api.uploadedBytes.contentEquals(photo()))
    }

    @Test
    fun recognize_vlmDisabled_flagsVlmDisabled_andStaysIdle() {
        api.next = SolveResponse(
            answer = "",
            recognizedText = "",
            vlmEnabled = false,
            degraded = true,
            message = "拍照识题未启用：未配置视觉模型"
        )
        vm.prepare(photo())
        vm.recognize()

        val ui = vm.ui.value
        assertEquals(CameraErrorKind.VlmDisabled, ui.errorKind)
        assertEquals("未启用时不能跳到结果页", CameraStage.Idle, ui.stage)
        assertTrue(ui.error!!.contains("未配置视觉模型"))
        assertTrue("未启用时不该有题干", ui.recognizedText.isBlank())
    }

    @Test
    fun recognize_emptyText_flagsEmptyText() {
        api.next = SolveResponse(
            answer = "",
            recognizedText = "   ",
            vlmEnabled = true,
            degraded = true,
            message = "没从这张图里识别出文字"
        )
        vm.prepare(photo())
        vm.recognize()

        assertEquals(CameraErrorKind.EmptyText, vm.ui.value.errorKind)
        assertEquals(CameraStage.Idle, vm.ui.value.stage)
        assertTrue(vm.ui.value.error!!.contains("没从这张图里识别出文字"))
    }

    @Test
    fun recognize_networkError_flagsNetwork_andKeepsPreview() {
        api.fail = RuntimeException("timeout")
        vm.prepare(photo())
        vm.recognize()

        val ui = vm.ui.value
        assertEquals(CameraErrorKind.Network, ui.errorKind)
        assertTrue(ui.error!!.contains("timeout"))
        assertTrue("失败后图还在，学生可直接重试", ui.preview != null)
    }

    @Test
    fun recognize_withoutPreview_doesNotCallApi() {
        vm.recognize()
        assertNull(api.uploadedBytes)
        assertTrue(vm.ui.value.error!!.contains("先拍一张"))
    }

    /** 识别不准是这个功能的头号风险，改题干必须被尊重。 */
    @Test
    fun updateText_overridesRecognizedText() {
        api.next = SolveResponse(answer = "", recognizedText = "认错的题面", vlmEnabled = true)
        vm.prepare(photo())
        vm.recognize()
        vm.updateText("学生手改成正确的题面")

        assertEquals("学生手改成正确的题面", vm.ui.value.recognizedText)
    }

    @Test
    fun reset_clearsEverything() {
        api.next = SolveResponse(answer = "", recognizedText = "题干", vlmEnabled = true)
        vm.prepare(photo())
        vm.recognize()
        vm.reset()

        assertEquals(CameraStage.Idle, vm.ui.value.stage)
        assertTrue(vm.ui.value.recognizedText.isEmpty())
        assertNull(vm.ui.value.preview)
        assertNull(vm.ui.value.errorKind)
    }
}
