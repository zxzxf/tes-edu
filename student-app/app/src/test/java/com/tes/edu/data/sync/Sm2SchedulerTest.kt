package com.tes.edu.data.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * SM-2 调度（纯 JVM）。
 *
 * 覆盖的是"会不会越背越离谱"的三件事：忘了要归零、ease 有下限、间隔要递增。
 * 评分对应 UI 四档：0=忘了 / 3=模糊 / 4=会了 / 5=太简单。
 */
class Sm2SchedulerTest {

    private val now = 1_700_000_000_000L
    private val day = 24L * 60 * 60 * 1000

    @Test
    fun forgot_resetsIntervalToOneDay_andCountsLapse() {
        val r = Sm2Scheduler.next(Sm2State(intervalDays = 20.0, reps = 5, lapses = 1), 0, now)
        assertEquals(1.0, r.intervalDays, 1e-9)
        assertEquals(2, r.lapses)
        assertEquals(now + day, r.dueAt)
    }

    @Test
    fun forgot_keepsReps_butLowersEase() {
        val r = Sm2Scheduler.next(Sm2State(reps = 5), 0, now)
        assertEquals(5, r.reps)
        assertEquals(2.3, r.ease, 1e-9)
    }

    @Test
    fun ease_hasFloorOfOnePointThree() {
        val state = Sm2State(ease = 1.3)
        val r = Sm2Scheduler.next(state, 0, now)
        assertEquals(1.3, r.ease, 1e-9)
        assertTrue("ease 不能再低，否则间隔塌缩到天天重复", r.ease >= 1.3)
    }

    @Test
    fun ratingFour_keepsEaseUnchanged() {
        // 0.1 - (5-4) * (0.08 + (5-4) * 0.02) = 0
        val r = Sm2Scheduler.next(Sm2State(), 4, now)
        assertEquals(2.5, r.ease, 1e-9)
    }

    @Test
    fun ratingFive_raisesEase() {
        val r = Sm2Scheduler.next(Sm2State(), 5, now)
        assertEquals(2.6, r.ease, 1e-9)
    }

    @Test
    fun ratingThree_lowersEase() {
        val r = Sm2Scheduler.next(Sm2State(), 3, now)
        assertEquals(2.36, r.ease, 1e-9)
    }

    @Test
    fun interval_grows_oneThenSixThenMultipliedByEase() {
        val first = Sm2Scheduler.next(Sm2State(), 4, now)
        assertEquals(1.0, first.intervalDays, 1e-9)
        assertEquals(1, first.reps)

        val second = Sm2Scheduler.next(Sm2State(ease = first.ease, intervalDays = first.intervalDays, reps = first.reps), 4, now)
        assertEquals(6.0, second.intervalDays, 1e-9)

        val third = Sm2Scheduler.next(
            Sm2State(ease = second.ease, intervalDays = second.intervalDays, reps = second.reps),
            4,
            now
        )
        assertEquals(15.0, third.intervalDays, 1e-9)
        assertTrue("间隔必须递增", third.intervalDays > second.intervalDays)
    }

    @Test
    fun dueAt_advancesByIntervalDays() {
        // 已复习 2 次（reps=2）→ 这次是第 3 次：interval = 6 * ease = 15 天
        val r = Sm2Scheduler.next(Sm2State(intervalDays = 6.0, reps = 2), 4, now)
        assertEquals(now + 15 * day, r.dueAt)
    }

    @Test
    fun afterLapse_relearningStartsOver() {
        // 忘了之后：间隔回 1 天，之后重新走 1 → 6 → *ease
        val forgot = Sm2Scheduler.next(Sm2State(intervalDays = 30.0, reps = 6), 0, now)
        val again = Sm2Scheduler.next(
            Sm2State(ease = forgot.ease, intervalDays = forgot.intervalDays, reps = 0, lapses = forgot.lapses),
            4,
            now
        )
        assertEquals(1.0, again.intervalDays, 1e-9)
        assertEquals(2.3, again.ease, 1e-9)
    }
}
