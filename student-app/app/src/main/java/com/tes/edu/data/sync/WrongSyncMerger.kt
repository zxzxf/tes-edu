package com.tes.edu.data.sync

import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.WrongPushItem
import com.tes.edu.data.remote.WrongRemote

/**
 * 错题本双向同步的合并逻辑（**纯 Kotlin，不碰 Android / 网络**）。
 *
 * 工作方式：本地 Room 是唯一数据源（离线可用），联网时先 pull 再 push。
 *
 * pull 合并规则：
 *  - 匹配：先按 `clientId`（本地生成、跨端一致），没有再按 `remoteId`；
 *  - 服务端 `deletedAt != null` → 本地真删（删除以云端为准，避免"删了又回来"）；
 *  - 匹配不到 → 视为新记录，落本地（dirty=false，刚从云端下来的）；
 *  - 冲突裁决按 `updatedAt` 大者胜，但**只保护本地未上传的改动**：
 *    本地 dirty 且 updatedAt 更大才留本地，其余一律以云端为准
 *    （本地已同步过的记录没有"我方更新"可言，让云端覆盖最省事）；
 *  - 本地胜出时顺手补 `remoteId`，之后是 update 而不是再 insert 一条。
 *
 * 抽成纯函数是为了 JVM 单测不用 Robolectric / 不连网也能覆盖合并规则。
 */
object WrongSyncMerger {

    /** 合并产出的落库动作。 */
    data class Plan(
        /** 要 upsert 的本地记录（带 id 即覆盖同 id 行）。 */
        val upserts: List<WrongQuestion> = emptyList(),
        /** 要从本地真删的 id。 */
        val deleteIds: List<Long> = emptyList()
    )

    /** pull：把服务端快照合并进本地。 */
    fun merge(local: List<WrongQuestion>, remote: List<WrongRemote>): Plan {
        val byClientId = HashMap<String, WrongQuestion>()
        val byRemoteId = HashMap<Long, WrongQuestion>()
        local.forEach { q ->
            q.clientId?.takeIf { it.isNotBlank() }?.let { byClientId[it] = q }
            q.remoteId?.let { byRemoteId[it] = q }
        }

        val upserts = mutableListOf<WrongQuestion>()
        val deleteIds = mutableListOf<Long>()

        for (r in remote) {
            val matched = r.clientId?.takeIf { it.isNotBlank() }?.let { byClientId[it] }
                ?: byRemoteId[r.id]
            when {
                r.deletedAt != null -> {
                    if (matched != null) deleteIds += matched.id
                }
                matched == null -> upserts += r.toLocal()
                localWins(matched, r) -> {
                    // 本地改动还没上传：留本地，补上服务端 id 让后续走更新
                    if (matched.remoteId == null) upserts += matched.copy(remoteId = r.id)
                }
                else -> upserts += r.toLocal(id = matched.id)
            }
        }
        return Plan(upserts, deleteIds)
    }

    /**
     * push：本地 dirty 记录 → 请求体。
     *
     * 没有 clientId 的记录不上行——服务端按 client_id 做 upsert，缺了它
     * 每次都会新建一条（调用方先用 [ensureClientIds] 补齐）。
     * `updatedAt` 为 0 是 v3 之前的老数据，用 now 兜底，否则永远判成"更旧"。
     */
    fun toPushItems(local: List<WrongQuestion>, now: Long = 0): List<WrongPushItem> =
        local.filter { it.dirty && !it.clientId.isNullOrBlank() }.map { q ->
            WrongPushItem(
                clientId = q.clientId!!,
                question = q.question,
                myAnswer = q.myAnswer,
                correctAnswer = q.correctAnswer,
                knowledgePoint = q.knowledgePoint.takeIf { it.isNotBlank() },
                errorType = q.errorType,
                comment = q.comment,
                correction = q.correction,
                firstWrongStep = q.firstWrongStep,
                updatedAt = if (q.updatedAt > 0) q.updatedAt else now,
                deleted = q.deleted
            )
        }

    /**
     * push 之后：按服务端回吐确认本地记录。
     * 回吐里没有的记录**保持 dirty**（宁可下次再传一遍，也不能当成已同步）。
     */
    fun applyPushResult(local: List<WrongQuestion>, remote: List<WrongRemote>): Plan {
        val echoed = HashMap<String, WrongRemote>()
        remote.forEach { r ->
            r.clientId?.takeIf { it.isNotBlank() }?.let { echoed[it] = r }
        }
        val upserts = mutableListOf<WrongQuestion>()
        val deleteIds = mutableListOf<Long>()
        for (q in local) {
            if (!q.dirty) continue
            val r = q.clientId?.let { echoed[it] } ?: continue
            if (q.deleted || r.deletedAt != null) {
                deleteIds += q.id // 服务端已软删，本地真删
            } else {
                upserts += q.copy(
                    remoteId = r.id,
                    dirty = false,
                    deleted = false,
                    updatedAt = maxOf(q.updatedAt, r.updatedAt)
                )
            }
        }
        return Plan(upserts, deleteIds)
    }

    /** 给缺 clientId 的本地记录（v3 前的老数据）补 UUID，返回需要写回的那些。 */
    fun ensureClientIds(
        local: List<WrongQuestion>,
        gen: () -> String = { java.util.UUID.randomUUID().toString() }
    ): List<WrongQuestion> =
        local.mapNotNull { q -> if (q.clientId.isNullOrBlank()) q.copy(clientId = gen()) else null }

    /** 本地胜出：有未上传改动，且比服务端记录新。 */
    private fun localWins(q: WrongQuestion, r: WrongRemote): Boolean =
        q.dirty && q.updatedAt > r.updatedAt
}

/** 服务端记录 → 本地记录；[id] 非空时覆盖对应行。 */
internal fun WrongRemote.toLocal(id: Long = 0): WrongQuestion = WrongQuestion(
    id = id,
    question = question,
    myAnswer = myAnswer.orEmpty(),
    correctAnswer = correctAnswer.orEmpty(),
    knowledgePoint = knowledgePoint.orEmpty(),
    createdAt = if (createdAt > 0) createdAt else updatedAt,
    errorType = errorType,
    firstWrongStep = firstWrongStep,
    comment = comment,
    correction = correction,
    remoteId = this.id,
    clientId = clientId,
    updatedAt = updatedAt,
    dirty = false,
    deleted = false
)
