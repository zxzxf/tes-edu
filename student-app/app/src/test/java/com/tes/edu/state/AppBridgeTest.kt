package com.tes.edu.state

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 跨 tab 传参桥接（T-S4/T-G1/T-G2）：一次性消费语义。
 *
 * 核心要锁的行为是"取出即清空"——否则学生从图谱跳一次输入页之后，
 * 之后每次回输入页都会被那道旧题顶掉自己正在写的题干。
 */
class AppBridgeTest {

    @After
    fun tearDown() = AppBridge.resetForTest()

    @Test
    fun postQuestion_thenConsume_returnsOnceAndClears() {
        AppBridge.postQuestion("求 f(x)=x^2-2x 的最小值")
        assertEquals("求 f(x)=x^2-2x 的最小值", AppBridge.consumeQuestion())
        assertNull("消费后必须清空", AppBridge.consumeQuestion())
    }

    @Test
    fun postQuestion_trimsAndIgnoresBlank() {
        AppBridge.postQuestion("   ")
        assertNull("空白题干不投递", AppBridge.consumeQuestion())
        AppBridge.postQuestion("  题目 A  ")
        assertEquals("题目 A", AppBridge.consumeQuestion())
    }

    @Test
    fun setLastQuestion_ignoresBlank() {
        AppBridge.setLastQuestion("")
        assertEquals("", AppBridge.lastQuestion.value)
        AppBridge.setLastQuestion("题目 B")
        assertEquals("题目 B", AppBridge.lastQuestion.value)
    }

    @Test
    fun postFocusKp_consumesPairOnce() {
        AppBridge.postFocusKp("MA-B4-C2-S1-K03", "二次函数的极值")
        val pair = AppBridge.consumeFocusKp()
        assertEquals("MA-B4-C2-S1-K03", pair!!.first)
        assertEquals("二次函数的极值", pair.second)
        assertNull("消费后必须清空", AppBridge.consumeFocusKp())
    }

    @Test
    fun postFocusKp_ignoresBlankCode() {
        AppBridge.postFocusKp("", "标题")
        assertNull(AppBridge.consumeFocusKp())
    }
}
