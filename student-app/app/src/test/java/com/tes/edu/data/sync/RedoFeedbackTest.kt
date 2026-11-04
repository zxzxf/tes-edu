package com.tes.edu.data.sync

import com.tes.edu.data.remote.WrongRedoResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** M3-9 重做文案与多空拆分（纯 JVM，不依赖 Room/网络）。 */
class RedoFeedbackTest {

    @Test
    fun resolvedWithCard_saysArchivedAndRescheduled() {
        val msg = redoFeedback(
            WrongRedoResult(status = "auto", correct = true, resolved = true, cardPushed = true)
        )
        assertTrue(msg.contains("归档"))
        assertTrue(msg.contains("背诵卡"))
    }

    @Test
    fun resolvedWithoutCard_stillArchives() {
        val msg = redoFeedback(
            WrongRedoResult(status = "auto", correct = true, resolved = true, cardPushed = false)
        )
        assertTrue(msg.contains("归档"))
        assertTrue("没有推卡就不该提背诵卡", !msg.contains("背诵卡"))
    }

    @Test
    fun manualPending_doesNotClaimWrong() {
        val msg = redoFeedback(
            WrongRedoResult(
                status = "manual_pending",
                correct = null,
                resolved = false,
                reason = "这道题的答案含字母或符号表达式"
            )
        )
        assertTrue(msg.contains("老师"))
        assertTrue("转人工不能说成答错", !msg.contains("答错"))
    }

    @Test
    fun unavailable_explainsSourceGone() {
        val msg = redoFeedback(
            WrongRedoResult(status = "unavailable", resolved = false, reason = "原题已不存在")
        )
        assertTrue(msg.contains("不可用"))
    }

    @Test
    fun partial_keepsQuestionInBook() {
        val msg = redoFeedback(
            WrongRedoResult(status = "auto", correct = false, partial = true, resolved = false)
        )
        assertTrue(msg.contains("部分正确"))
        assertTrue(msg.contains("留在本里"))
    }

    @Test
    fun wrong_keepsQuestionInBook() {
        val msg = redoFeedback(
            WrongRedoResult(status = "auto", correct = false, resolved = false)
        )
        assertTrue(msg.contains("还不全对"))
    }

    @Test
    fun unknownStatus_fallsBackGracefully() {
        val msg = redoFeedback(WrongRedoResult(status = "", resolved = false))
        assertTrue(msg.isNotBlank())
        assertTrue(!msg.contains("答错"))
    }

    @Test
    fun singleAnswer_isNotSplit() {
        assertEquals(emptyList<String>(), splitRedoBlanks("A"))
        assertEquals(emptyList<String>(), splitRedoBlanks("1,234"))
        assertEquals(emptyList<String>(), splitRedoBlanks("[1,2]"))
    }

    @Test
    fun multiAnswers_splitByChineseSemicolon() {
        assertEquals(listOf("1", "2"), splitRedoBlanks("1；2"))
        assertEquals(listOf("1", "2"), splitRedoBlanks(" 1 ; 2 "))
    }

    @Test
    fun multiAnswers_splitByNewlineOrPipe() {
        assertEquals(listOf("x=1", "y=2"), splitRedoBlanks("x=1\ny=2"))
        assertEquals(listOf("x=1", "y=2"), splitRedoBlanks("x=1|y=2"))
    }

    @Test
    fun blankInput_yieldsNoBlanks() {
        assertEquals(emptyList<String>(), splitRedoBlanks("   "))
    }
}
