package com.tes.edu.data.sync

/**
 * SM-2 间隔重复调度（简版，与后端 drill-cards review 同一套规则）。
 *
 * 规则（写死在注释里，改之前先对齐后端）：
 *  - rating < 3（忘了 / 模糊档里更低的那档）：lapses+1，interval 归 1 天，ease-0.2；
 *  - rating >= 3：ease += 0.1 - (5-rating) * (0.08 + (5-rating) * 0.02)，
 *    reps+1，interval = 第 1 次 1 天、第 2 次 6 天、之后 interval * ease；
 *  - ease 下限 1.3（再低间隔会塌缩到几乎天天重复，学生直接放弃）。
 *
 * 为什么本地也要算一遍：离线时学生照样能背，评分排进 `drill_reviews` 队列，
 * 联网后按 remoteId 补传，服务端重算的权威值回来再覆盖。
 */
data class Sm2State(
    val ease: Double = DEFAULT_EASE,
    val intervalDays: Double = 0.0,
    val reps: Int = 0,
    val lapses: Int = 0
) {
    companion object {
        const val DEFAULT_EASE = 2.5
        const val MIN_EASE = 1.3
    }
}

/** 一次复习的结果：新的调度参数 + 下次到期时间。 */
data class Sm2Result(
    val ease: Double,
    val intervalDays: Double,
    val reps: Int,
    val lapses: Int,
    val dueAt: Long
)

object Sm2Scheduler {

    private const val DAY_MILLIS = 24L * 60L * 60L * 1000L

    /**
     * 计算一次评分后的调度。
     *
     * @param rating 0=忘了 / 3=模糊 / 4=会了 / 5=太简单
     * @param now 复习发生的时刻（epoch millis），dueAt 以它为基准
     */
    fun next(state: Sm2State, rating: Int, now: Long): Sm2Result {
        if (rating < 3) {
            // 忘了：从 1 天重新开始，但记一笔 lapse（这张卡确实啃不动）
            val ease = maxOf(Sm2State.MIN_EASE, state.ease - 0.2)
            return Sm2Result(
                ease = ease,
                intervalDays = 1.0,
                reps = state.reps,
                lapses = state.lapses + 1,
                dueAt = now + DAY_MILLIS
            )
        }
        val q = 5 - rating
        val ease = maxOf(Sm2State.MIN_EASE, state.ease + (0.1 - q * (0.08 + q * 0.02)))
        val reps = state.reps + 1
        val interval = when (reps) {
            1 -> 1.0
            2 -> 6.0
            else -> (state.intervalDays * ease).coerceAtLeast(1.0)
        }
        return Sm2Result(
            ease = ease,
            intervalDays = interval,
            reps = reps,
            lapses = state.lapses,
            dueAt = now + (interval * DAY_MILLIS).toLong()
        )
    }
}
