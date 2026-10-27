package com.tes.edu.data.sync

import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.remote.DrillCardRemote
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 背诵卡同步合并（纯 JVM）：排程以服务端为准，本地新建卡只回填 id。 */
class DrillSyncMergerTest {

    private fun local(
        id: Long = 1,
        clientId: String? = "c1",
        remoteId: Long? = null,
        dirty: Boolean = false
    ) = DrillCard(
        id = id,
        title = "本地正面",
        content = "本地背面",
        clientId = clientId,
        remoteId = remoteId,
        kp = "MA-B4-C2-S1-K03",
        front = "本地正面",
        back = "本地背面",
        dueAt = 100L,
        dirty = dirty
    )

    private fun remote(
        id: Long = 9,
        clientId: String? = "c1",
        dueAt: Long = 200L,
        deletedAt: Long? = null
    ) = DrillCardRemote(
        id = id,
        clientId = clientId,
        kp = "MA-B4-C2-S1-K03",
        front = "云端正面",
        back = "云端背面",
        dueAt = dueAt,
        ease = 2.4,
        intervalDays = 3.0,
        reps = 2,
        lapses = 1,
        deletedAt = deletedAt
    )

    @Test
    fun merge_serverDeleted_removesLocal() {
        val plan = DrillSyncMerger.merge(listOf(local()), listOf(remote(deletedAt = 500L)))
        assertEquals(listOf(1L), plan.deleteIds)
        assertTrue(plan.upserts.isEmpty())
    }

    @Test
    fun merge_syncsSchedulingFromServer() {
        val plan = DrillSyncMerger.merge(listOf(local()), listOf(remote()))
        val card = plan.upserts.single()
        assertEquals(1L, card.id)
        assertEquals("云端正面", card.front)
        assertEquals(200L, card.dueAt)
        assertEquals(2.4, card.ease, 1e-9)
        assertEquals(3.0, card.intervalDays, 1e-9)
        assertEquals(2, card.reps)
    }

    @Test
    fun merge_localNewCard_onlyBackfillsRemoteId() {
        val plan = DrillSyncMerger.merge(listOf(local(dirty = true)), listOf(remote()))
        val card = plan.upserts.single()
        assertEquals("本地正面", card.front)
        assertEquals(9L, card.remoteId)
        assertFalse(card.dirty)
    }

    @Test
    fun merge_unknownRemote_insertsNewCard() {
        val plan = DrillSyncMerger.merge(emptyList(), listOf(remote(clientId = "c8")))
        assertEquals(0L, plan.upserts.single().id)
        assertEquals("c8", plan.upserts.single().clientId)
    }

    @Test
    fun toPushItems_usesFrontBackFallback() {
        // 老数据只有 title/content：上行也要有完整正反面
        val legacy = DrillCard(id = 3, title = "老正面", content = "老背面", clientId = "c3", dirty = true)
        val items = DrillSyncMerger.toPushItems(listOf(legacy, local(dirty = true), local(id = 4, dirty = false)))
        assertEquals(2, items.size)
        assertEquals("老正面", items.first().front)
        assertEquals("老背面", items.first().back)
        assertEquals("MA-B4-C2-S1-K03", items.last().kp)
    }

    @Test
    fun applyPushResult_clearsDirtyAndTakesServerSchedule() {
        val plan = DrillSyncMerger.applyPushResult(listOf(local(dirty = true)), listOf(remote()))
        val card = plan.upserts.single()
        assertFalse(card.dirty)
        assertEquals(9L, card.remoteId)
        assertEquals(200L, card.dueAt)
    }

    @Test
    fun applyPushResult_notEchoed_keepsDirty() {
        assertTrue(DrillSyncMerger.applyPushResult(listOf(local(dirty = true)), emptyList()).upserts.isEmpty())
    }
}
