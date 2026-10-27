package com.tes.edu.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.local.AppDatabase
import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.LearningApi
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.StudentSession
import com.tes.edu.data.remote.WrongStats
import com.tes.edu.data.remote.WrongSyncRequest
import com.tes.edu.data.sync.WrongSyncMerger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.util.UUID

/** 断网时的轻提示——同步失败不弹错、不打断复习，只说一句"联网后会自动来"。 */
const val OFFLINE_HINT = "离线中，联网后自动同步"

/** 错题筛选条件：kp=知识点，errorType=错因；都为 null 表示不筛选。 */
data class WrongFilter(
    val kp: String? = null,
    val errorType: String? = null
) {
    val active: Boolean get() = kp != null || errorType != null

    fun matches(q: WrongQuestion): Boolean =
        (kp == null || q.knowledgePoint == kp) && (errorType == null || q.errorType == errorType)
}

/**
 * 错题本（T-R）：本地 Room 是唯一数据源（离线可翻），联网时与后端双向同步。
 *
 * 只存"学生真正想回头看的"——判分结果出现时由输入页手动收进，
 * 不做自动全量归档：自动收会把随手一试的题也塞进来，错题本很快失去意义。
 *
 * 同步时机：进入错题页、新增/删除后各触发一次 [sync]；不做后台定时轮询
 * （学生端没这个必要，省电也要省）。
 */
// @JvmOverloads：Kotlin 只在**全部**参数都有默认值时才生成无参构造；这里 app 没有
// 默认值，必须让编译器额外产出 (Application) 构造，否则 ViewModelProvider 的默认
// 工厂找不到构造函数，进页面就崩。
class WrongBookViewModel @JvmOverloads constructor(
    app: Application,
    private val api: LearningApi = ApiClient.learning
) : AndroidViewModel(app) {

    private val dao = AppDatabase.get(app).wrongQuestionDao()

    /** 未软删的错题，最近错的排在最前。 */
    val items: StateFlow<List<WrongQuestion>> = dao.all()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    private val _filter = MutableStateFlow(WrongFilter())
    val filter: StateFlow<WrongFilter> = _filter.asStateFlow()

    /** 筛选后的列表。数据量小（个人错题本），客户端过滤即可，不必反复打后端。 */
    val visible: StateFlow<List<WrongQuestion>> =
        combine(items, _filter) { list, f -> list.filter { f.matches(it) } }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** 知识点筛选项：本地记录里去重的 kp（离线也有，不依赖后端）。 */
    val kpOptions: StateFlow<List<String>> = items
        .map { list -> list.mapNotNull { it.knowledgePoint.takeIf { kp -> kp.isNotBlank() } } }
        .map { it.distinct().sorted() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /** 未同步条数（dirty 计数），UI 顶部展示。 */
    val hasPending: StateFlow<Int> = dao.dirtyCount()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), 0)

    private val _stats = MutableStateFlow<WrongStats?>(null)
    val stats: StateFlow<WrongStats?> = _stats.asStateFlow()

    private val _syncing = MutableStateFlow(false)
    val syncing: StateFlow<Boolean> = _syncing.asStateFlow()

    /** 同步失败提示（断网时是 [OFFLINE_HINT]，不算错误态）。 */
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    /** 切换筛选：本地立即生效，同时让后端也按同一条件过滤下一轮 pull。 */
    fun setFilter(f: WrongFilter) {
        if (_filter.value == f) return
        _filter.value = f
        sync()
    }

    /**
     * 收一道错题。
     * @return false 表示同题干已经在错题本里了（不重复收，避免刷屏）。
     */
    private suspend fun add(q: WrongQuestion): Boolean {
        if (q.question.isBlank()) return false
        if (dao.countByQuestion(q.question) > 0) return false
        dao.insert(q.stampedForSync())
        return true
    }

    /** 供 UI 调用：[onDone] 的入参是"是否真的收进去了"。 */
    fun save(q: WrongQuestion, onDone: (added: Boolean) -> Unit) {
        viewModelScope.launch {
            val added = add(q)
            onDone(added)
            if (added) sync()
        }
    }

    /**
     * 移出错题本：走软删（置 deleted + dirty），同步把删除上传成功后本地才真删。
     * 直接真删的话，断网时删掉的题下次同步又会被服务端"复活"。
     */
    fun remove(q: WrongQuestion) {
        viewModelScope.launch {
            dao.upsert(
                q.copy(
                    deleted = true,
                    dirty = true,
                    updatedAt = System.currentTimeMillis()
                )
            )
            sync()
        }
    }

    /** 清空本地错题本（真删；同步后服务端记录仍在，谨慎使用）。 */
    fun clear() {
        viewModelScope.launch { dao.clear() }
    }

    /**
     * 双向同步：先 pull（增量，按 clientId/remoteId 合并）再 push（本地 dirty）。
     *
     * 任何异常都在这里吞掉并保留 dirty——断网是常态，不该让页面崩或弹红错。
     */
    fun sync() {
        viewModelScope.launch {
            if (_syncing.value) return@launch // 简单防重入：连点/多处触发只跑一次
            _syncing.value = true
            try {
                if (!LoginManager.ensureLogin()) {
                    _error.value = "未登录，登录后再同步"
                    return@launch
                }
                val sid = StudentSession.currentId()
                if (sid == null) {
                    _error.value = "未登录，登录后再同步"
                    return@launch
                }

                // —— pull ——
                val res = api.wrongQuestions(
                    studentId = sid,
                    since = StudentSession.wrongSince(),
                    kp = _filter.value.kp,
                    errorType = _filter.value.errorType
                )
                apply(WrongSyncMerger.merge(dao.allOnce(), res.items))
                StudentSession.markWrongSync(res.serverTime)

                // —— push ——
                // 老数据（v3 前）没有 clientId：先补齐再上行，否则服务端每次都当新记录
                val patched = WrongSyncMerger.ensureClientIds(dao.dirtyOnce())
                if (patched.isNotEmpty()) dao.upsertAll(patched)
                val dirty = dao.dirtyOnce()
                if (dirty.isNotEmpty()) {
                    val pushed = api.syncWrongQuestions(
                        WrongSyncRequest(sid, WrongSyncMerger.toPushItems(dirty, System.currentTimeMillis()))
                    )
                    apply(WrongSyncMerger.applyPushResult(dirty, pushed.items))
                }

                // 统计拿不到就算了（隐藏统计区），不影响错题本身
                _stats.value = runCatching { api.wrongStats(sid) }.getOrNull()
                _error.value = null
            } catch (e: Exception) {
                _error.value = OFFLINE_HINT
            } finally {
                _syncing.value = false
            }
        }
    }

    private suspend fun apply(plan: WrongSyncMerger.Plan) {
        if (plan.upserts.isNotEmpty()) dao.upsertAll(plan.upserts)
        if (plan.deleteIds.isNotEmpty()) dao.deleteByIds(plan.deleteIds)
    }

    /** 打上同步所需的本地标记：clientId 是跨端对账的主键。 */
    private fun WrongQuestion.stampedForSync(): WrongQuestion = copy(
        clientId = clientId?.takeIf { it.isNotBlank() } ?: UUID.randomUUID().toString(),
        updatedAt = System.currentTimeMillis(),
        dirty = true,
        deleted = false
    )
}
