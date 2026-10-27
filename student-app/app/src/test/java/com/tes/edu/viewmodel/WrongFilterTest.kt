package com.tes.edu.viewmodel

import com.tes.edu.data.local.entities.WrongQuestion
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 错题筛选（客户端过滤，纯 JVM）。 */
class WrongFilterTest {

    private fun q(kp: String = "KP-A", errorType: String? = "calc") = WrongQuestion(
        id = 1,
        question = "题",
        myAnswer = "",
        correctAnswer = "",
        knowledgePoint = kp,
        errorType = errorType
    )

    @Test
    fun emptyFilter_matchesEverything() {
        assertTrue(WrongFilter().matches(q()))
        assertTrue(WrongFilter().matches(q(errorType = null)))
        assertFalse(WrongFilter().active)
    }

    @Test
    fun filterByKp_onlySameKp() {
        val f = WrongFilter(kp = "KP-A")
        assertTrue(f.active)
        assertTrue(f.matches(q(kp = "KP-A")))
        assertFalse(f.matches(q(kp = "KP-B")))
    }

    @Test
    fun filterByErrorType_onlySameType() {
        val f = WrongFilter(errorType = "calc")
        assertTrue(f.matches(q(errorType = "calc")))
        assertFalse(f.matches(q(errorType = "read")))
        assertFalse("没有错因的题不该被错因筛选命中", f.matches(q(errorType = null)))
    }

    @Test
    fun filterByBoth_mustMatchBoth() {
        val f = WrongFilter(kp = "KP-A", errorType = "read")
        assertFalse(f.matches(q(kp = "KP-A", errorType = "calc")))
        assertFalse(f.matches(q(kp = "KP-B", errorType = "read")))
        assertTrue(f.matches(q(kp = "KP-A", errorType = "read")))
    }
}
