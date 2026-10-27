package com.tes.edu.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.tes.edu.data.local.AppDatabase
import com.tes.edu.data.local.entities.WrongQuestion
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * 错题本（T-R）：本地 Room 缓存，断网也能翻。
 *
 * 只存"学生真正想回头看的"——判分结果出现时由输入页手动收进，
 * 不做自动全量归档：自动收会把随手一试的题也塞进来，错题本很快失去意义。
 */
class WrongBookViewModel(app: Application) : AndroidViewModel(app) {
    private val dao = AppDatabase.get(app).wrongQuestionDao()

    /** 最近错的排在最前。 */
    val items: StateFlow<List<WrongQuestion>> = dao.all()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    /**
     * 收一道错题。
     * @return false 表示同题干已经在错题本里了（不重复收，避免刷屏）。
     */
    private suspend fun add(q: WrongQuestion): Boolean {
        if (q.question.isBlank()) return false
        if (dao.countByQuestion(q.question) > 0) return false
        dao.insert(q)
        return true
    }

    /** 供 UI 调用：[onDone] 的入参是"是否真的收进去了"。 */
    fun save(q: WrongQuestion, onDone: (added: Boolean) -> Unit) {
        viewModelScope.launch { onDone(add(q)) }
    }

    fun remove(q: WrongQuestion) {
        viewModelScope.launch { dao.delete(q) }
    }

    fun clear() {
        viewModelScope.launch { dao.clear() }
    }
}
