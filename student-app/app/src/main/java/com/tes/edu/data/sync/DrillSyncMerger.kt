package com.tes.edu.data.sync

import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.remote.DrillCardRemote
import com.tes.edu.data.remote.DrillPushItem

/**
 * 背诵卡同步的合并逻辑（**纯 Kotlin**）。
 *
 * 与错题本同构，只有一处不同：**排程以服务端为准**。
 * 复习评分是"服务端重算 + 回吐权威值"的模式（本地只是先按 SM-2 顶一下，
 * 评分本身还会补传），所以 pull 到记录就直接覆盖排程字段，
 * 否则离线复习的本地结果会和云端算出来的越差越远。
 *
 * 例外：本地新建还没上传的卡（dirty）保留本地内容，只回填 remoteId。
 */
object DrillSyncMerger {

    data class Plan(
        val upserts: List<DrillCard> = emptyList(),
        val deleteIds: List<Long> = emptyList()
    )

    fun merge(local: List<DrillCard>, remote: List<DrillCardRemote>): Plan {
        val byClientId = HashMap<String, DrillCard>()
        val byRemoteId = HashMap<Long, DrillCard>()
        local.forEach { c ->
            c.clientId?.takeIf { it.isNotBlank() }?.let { byClientId[it] = c }
            c.remoteId?.let { byRemoteId[it] = c }
        }
        val upserts = mutableListOf<DrillCard>()
        val deleteIds = mutableListOf<Long>()
        for (r in remote) {
            val matched = r.clientId?.takeIf { it.isNotBlank() }?.let { byClientId[it] }
                ?: byRemoteId[r.id]
            when {
                r.deletedAt != null -> if (matched != null) deleteIds += matched.id
                matched == null -> upserts += r.toLocal()
                matched.dirty -> upserts += matched.copy(remoteId = r.id, dirty = false)
                else -> upserts += r.toLocal(id = matched.id)
            }
        }
        return Plan(upserts, deleteIds)
    }

    /** 本地新建卡 → 上行请求体（没有 clientId 的传上去会被当成孤儿，先补齐）。 */
    fun toPushItems(local: List<DrillCard>): List<DrillPushItem> =
        local.filter { it.dirty && !it.clientId.isNullOrBlank() }.map { c ->
            DrillPushItem(
                clientId = c.clientId!!,
                kp = c.kp?.takeIf { it.isNotBlank() },
                front = c.frontText,
                back = c.backText,
                source = null
            )
        }

    /** push 之后回填 remoteId、清 dirty。 */
    fun applyPushResult(local: List<DrillCard>, remote: List<DrillCardRemote>): Plan {
        val echoed = HashMap<String, DrillCardRemote>()
        remote.forEach { r -> r.clientId?.takeIf { it.isNotBlank() }?.let { echoed[it] = r } }
        val upserts = mutableListOf<DrillCard>()
        for (c in local) {
            if (!c.dirty) continue
            val r = c.clientId?.let { echoed[it] } ?: continue
            upserts += c.copy(
                remoteId = r.id,
                dirty = false,
                deleted = false,
                // 服务端排程是权威：首次上传后的 due/ease 直接用回吐值
                dueAt = r.dueAt.takeIf { it > 0 } ?: c.dueAt,
                ease = r.ease,
                intervalDays = r.intervalDays,
                reps = r.reps,
                lapses = r.lapses
            )
        }
        return Plan(upserts)
    }

    /** 给缺 clientId 的本地卡补 UUID，返回需要写回的那些。 */
    fun ensureClientIds(
        local: List<DrillCard>,
        gen: () -> String = { java.util.UUID.randomUUID().toString() }
    ): List<DrillCard> =
        local.mapNotNull { c -> if (c.clientId.isNullOrBlank()) c.copy(clientId = gen()) else null }
}

/** 服务端卡 → 本地卡；[id] 非空时覆盖对应行。 */
internal fun DrillCardRemote.toLocal(id: Long = 0): DrillCard = DrillCard(
    id = id,
    // 老字段也写上：front/back 为空的展示兜底要用，且不影响既有列
    title = front,
    content = back,
    reviewAt = dueAt,
    remoteId = this.id,
    clientId = clientId,
    kp = kp,
    front = front,
    back = back,
    ease = ease,
    intervalDays = intervalDays,
    dueAt = dueAt,
    reps = reps,
    lapses = lapses,
    updatedAt = dueAt,
    dirty = false,
    deleted = false
)
