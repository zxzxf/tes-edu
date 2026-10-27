package com.tes.edu.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.local.AppDatabase
import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.local.entities.DrillReviewLog
import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.DrillPushRequest
import com.tes.edu.data.remote.DrillReviewRequest
import com.tes.edu.data.remote.DrillStats
import com.tes.edu.data.remote.LearningApi
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.StudentSession
import com.tes.edu.data.sync.DrillSyncMerger
import com.tes.edu.data.sync.Sm2Result
import com.tes.edu.data.sync.Sm2Scheduler
import com.tes.edu.data.sync.Sm2State
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

/** 一次最多背多少张：学生端是"顺手背几张"，不是刷题库。 */
private const val DRILL_LIMIT = 20

/** 背诵页状态。 */
data class DrillUiState(
    val loading: Boolean = false,
    val syncing: Boolean = false,
    /** 同步失败提示（断网是 [OFFLINE_HINT]，不算错误）。 */
    val error: String? = null,
    /** 本次复习队列（本地到期卡，离线也按这个背）。 */
    val queue: List<DrillCard> = emptyList(),
    val index: Int = 0,
    /** 今日（本次）已复习张数。 */
    val reviewed: Int = 0,
    /** 是否已进入复习流程。 */
    val started: Boolean = false,
    /** 队列背完。 */
    val finished: Boolean = false,
    /** 全库最近一次到期时间（完成态展示"下次什么时候来"）。 */
    val nextDueAt: Long? = null,
    val stats: DrillStats? = null
) {
    val dueCount: Int get() = queue.size
    val current: DrillCard? get() = queue.getOrNull(index)
}

/**
 * 背诵卡（间隔复习）。
 *
 * 工作模式与错题本一致：**本地优先**——到期的卡从本地 Room 取，离线照背；
 * 联网时才拉服务端卡、上传本地新建卡、并把离线攒下的评分补传上去。
 *
 * 评分先在本地按 SM-2 生效（[Sm2Scheduler]），同时排进 `drill_reviews` 队列；
 * 补传成功后用服务端回吐的权威排程覆盖本地。
 */
// @JvmOverloads 同 WrongBookViewModel：补出 (Application) 构造给默认 ViewModel 工厂。
class DrillViewModel @JvmOverloads constructor(
    app: Application,
    private val api: LearningApi = ApiClient.learning
) : AndroidViewModel(app) {

    private val dao = AppDatabase.get(app).drillCardDao()
    private val reviewDao = AppDatabase.get(app).drillReviewDao()

    private val _ui = MutableStateFlow(DrillUiState())
    val ui: StateFlow<DrillUiState> = _ui.asStateFlow()

    /** 进入页面：先按本地队列渲染（离线可用），再尝试同步刷新。 */
    fun load() {
        viewModelScope.launch {
            _ui.update { it.copy(loading = true, error = null) }
            refreshQueue()
            sync()
            _ui.update { it.copy(loading = false) }
        }
    }

    /** 点「开始复习」：进入复习流程，从头开始。 */
    fun start() {
        _ui.update {
            it.copy(started = true, finished = false, index = 0, reviewed = 0)
        }
    }

    /** 从复习流程退回概览（完成态之后）。 */
    fun reset() {
        _ui.update { it.copy(started = false, finished = false, index = 0, reviewed = 0) }
        refreshQueueAsync()
    }

    /**
     * 提交一次自评：0=忘了 / 3=模糊 / 4=会了 / 5=太简单。
     *
     * 本地立即按 SM-2 更新（离线也生效），评分入队待补传，然后翻到下一张。
     */
    fun grade(rating: Int) {
        viewModelScope.launch {
            val card = _ui.value.current ?: return@launch
            val now = System.currentTimeMillis()
            val next = Sm2Scheduler.next(
                Sm2State(card.ease, card.intervalDays, card.reps, card.lapses),
                rating,
                now
            )
            dao.upsert(card.applyResult(next, now))
            reviewDao.insert(
                DrillReviewLog(
                    cardRemoteId = card.remoteId,
                    clientId = card.clientId.orEmpty(),
                    rating = rating,
                    createdAt = now
                )
            )
            val nextIndex = _ui.value.index + 1
            _ui.update {
                it.copy(
                    index = nextIndex,
                    reviewed = it.reviewed + 1,
                    finished = nextIndex >= it.queue.size
                )
            }
            if (_ui.value.finished) {
                _ui.update { it.copy(nextDueAt = dao.nextDueAt(now)) }
            }
            // 联网就顺手补传；断网则留在队列里，下次进入页面再试
            flushReviews()
        }
    }

    /**
     * 从错题「做成背诵卡」：正面=题干，背面=纠正（没有就用参考答案），
     * kp 沿用错题的知识点，创建当天即到期（错题转的卡应该尽快过一遍）。
     */
    fun addFromWrong(q: WrongQuestion, onDone: (added: Boolean) -> Unit) {
        viewModelScope.launch {
            if (q.question.isBlank()) {
                onDone(false)
                return@launch
            }
            val now = System.currentTimeMillis()
            val back = q.correction?.takeIf { it.isNotBlank() } ?: q.correctAnswer
            dao.insert(
                DrillCard(
                    title = q.question,
                    content = back,
                    reviewAt = now,
                    clientId = UUID.randomUUID().toString(),
                    kp = q.knowledgePoint.takeIf { it.isNotBlank() },
                    front = q.question,
                    back = back,
                    dueAt = now,
                    updatedAt = now,
                    dirty = true
                )
            )
            onDone(true)
            sync()
        }
    }

    /**
     * 与服务端对账：pull 到期卡 → push 本地新建卡 → 补传离线评分 → 拉统计。
     * 全部异常都吞掉：断网时本地队列照常可用。
     */
    fun sync() {
        viewModelScope.launch {
            _ui.update { it.copy(syncing = true) }
            try {
                if (!LoginManager.ensureLogin()) {
                    _ui.update { it.copy(error = "未登录，登录后再同步") }
                    return@launch
                }
                val sid = StudentSession.currentId() ?: run {
                    _ui.update { it.copy(error = "未登录，登录后再同步") }
                    return@launch
                }

                val res = api.drillCards(studentId = sid, dueOnly = 1, limit = DRILL_LIMIT)
                apply(DrillSyncMerger.merge(dao.allOnce(), res.items))

                val patched = DrillSyncMerger.ensureClientIds(dao.dirtyOnce())
                if (patched.isNotEmpty()) dao.upsertAll(patched)
                val dirty = dao.dirtyOnce()
                if (dirty.isNotEmpty()) {
                    val pushed = api.pushDrillCards(
                        DrillPushRequest(sid, DrillSyncMerger.toPushItems(dirty))
                    )
                    apply(DrillSyncMerger.applyPushResult(dirty, pushed.items))
                }

                flushReviews()
                _ui.update {
                    it.copy(
                        stats = runCatching { api.drillStats(sid) }.getOrNull(),
                        error = null
                    )
                }
                refreshQueue()
            } catch (e: Exception) {
                _ui.update { it.copy(error = OFFLINE_HINT) }
            } finally {
                _ui.update { it.copy(syncing = false) }
            }
        }
    }

    /** 补传离线攒下的评分；一条失败就停下（顺序提交，后面的依赖前面的结果）。 */
    private suspend fun flushReviews() {
        if (!LoginManager.ensureLogin()) return
        val sid = StudentSession.currentId() ?: return
        for (log in reviewDao.all()) {
            val rid = log.cardRemoteId ?: continue // 卡还没上传：等 sync 拿到 id
            val res = runCatching {
                api.reviewDrillCard(rid, sid, DrillReviewRequest(log.rating))
            }.getOrElse { return } // 断网：保留队列，下次再补
            dao.byRemoteId(rid)?.let { card ->
                dao.upsert(
                    card.copy(
                        dueAt = res.dueAt,
                        intervalDays = res.intervalDays,
                        ease = res.ease,
                        reps = res.reps,
                        lapses = res.lapses,
                        updatedAt = System.currentTimeMillis()
                    )
                )
            }
            reviewDao.delete(log.id)
        }
    }

    private suspend fun refreshQueue() {
        val now = System.currentTimeMillis()
        val queue = dao.dueOnce(now, DRILL_LIMIT)
        _ui.update {
            it.copy(
                queue = queue,
                // 队列被同步刷新过（新卡进来 / 卡被删）时不要把 index 顶出界
                index = it.index.coerceAtMost((queue.size - 1).coerceAtLeast(0)),
                finished = it.started && it.index >= queue.size
            )
        }
    }

    private fun refreshQueueAsync() {
        viewModelScope.launch { refreshQueue() }
    }

    private suspend fun apply(plan: DrillSyncMerger.Plan) {
        if (plan.upserts.isNotEmpty()) dao.upsertAll(plan.upserts)
        if (plan.deleteIds.isNotEmpty()) dao.deleteByIds(plan.deleteIds)
    }

    private fun DrillCard.applyResult(r: Sm2Result, now: Long): DrillCard = copy(
        ease = r.ease,
        intervalDays = r.intervalDays,
        reps = r.reps,
        lapses = r.lapses,
        dueAt = r.dueAt,
        updatedAt = now
    )
}
