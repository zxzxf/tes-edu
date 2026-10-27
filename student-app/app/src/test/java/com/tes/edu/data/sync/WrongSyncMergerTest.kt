package com.tes.edu.data.sync

import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.WrongRemote
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 错题同步合并规则（纯 JVM，不连网、不依赖 Android 框架）。
 *
 * 这些规则是"本地是唯一数据源"能成立的前提：删错一条就会丢学生的错题，
 * 冲突判反就会把本地改动无声覆盖掉，所以逐条钉死。
 */
class WrongSyncMergerTest {

    private fun local(
        id: Long = 1,
        clientId: String? = "c1",
        remoteId: Long? = null,
        updatedAt: Long = 100L,
        dirty: Boolean = false,
        deleted: Boolean = false,
        question: String = "本地题干"
    ) = WrongQuestion(
        id = id,
        question = question,
        myAnswer = "我的作答",
        correctAnswer = "参考答案",
        knowledgePoint = "MA-B4-C2-S1-K03",
        errorType = "calc",
        clientId = clientId,
        remoteId = remoteId,
        updatedAt = updatedAt,
        dirty = dirty,
        deleted = deleted
    )

    private fun remote(
        id: Long = 9,
        clientId: String? = "c1",
        updatedAt: Long = 200L,
        question: String = "云端题干",
        deletedAt: Long? = null
    ) = WrongRemote(
        id = id,
        clientId = clientId,
        question = question,
        myAnswer = "云端作答",
        correctAnswer = "云端答案",
        knowledgePoint = "MA-B4-C2-S1-K03",
        errorType = "calc",
        createdAt = 10L,
        updatedAt = updatedAt,
        deletedAt = deletedAt
    )

    // ------------------------------------------------------------ pull 合并

    @Test
    fun merge_serverDeleted_removesLocalRow() {
        val plan = WrongSyncMerger.merge(listOf(local()), listOf(remote(deletedAt = 500L)))
        assertEquals(listOf(1L), plan.deleteIds)
        assertTrue("已删的记录不该再写回本地", plan.upserts.isEmpty())
    }

    @Test
    fun merge_serverDeletedForUnknownClient_doesNotResurrect() {
        val plan = WrongSyncMerger.merge(
            listOf(local(clientId = "c1")),
            listOf(remote(clientId = "c9", deletedAt = 5L))
        )
        assertTrue(plan.deleteIds.isEmpty())
        assertTrue(
            "服务端已删、本地又没有的记录不该被当成新错题插回来",
            plan.upserts.isEmpty()
        )
    }

    @Test
    fun merge_remoteNewer_overwritesLocal() {
        val plan = WrongSyncMerger.merge(
            listOf(local(updatedAt = 100L, dirty = false)),
            listOf(remote(updatedAt = 200L, question = "云端题干"))
        )
        val upsert = plan.upserts.single()
        assertEquals(1L, upsert.id)
        assertEquals("云端题干", upsert.question)
        assertEquals(9L, upsert.remoteId)
        assertFalse("刚从云端下来的不该再标脏", upsert.dirty)
    }

    @Test
    fun merge_localDirtyAndNewer_keepsLocalAndBackfillsRemoteId() {
        val plan = WrongSyncMerger.merge(
            listOf(local(updatedAt = 300L, dirty = true, question = "本地题干")),
            listOf(remote(updatedAt = 200L, question = "云端题干"))
        )
        val upsert = plan.upserts.single()
        assertEquals("本地题干", upsert.question)
        assertEquals(9L, upsert.remoteId)
        assertTrue("本地改动还没上传，dirty 必须留着", upsert.dirty)
    }

    @Test
    fun merge_localNotDirty_remoteAlwaysWins() {
        // 本地虽然 updatedAt 更大，但已经同步过（不脏）——没有"我方未上传的改动"，
        // 以云端为准，避免两边各改一次后本地永远压着云端
        val plan = WrongSyncMerger.merge(
            listOf(local(updatedAt = 300L, dirty = false, question = "本地题干")),
            listOf(remote(updatedAt = 200L, question = "云端题干"))
        )
        assertEquals("云端题干", plan.upserts.single().question)
    }

    @Test
    fun merge_unknownRemote_insertsAsNew() {
        val plan = WrongSyncMerger.merge(emptyList(), listOf(remote(clientId = "c7")))
        val upsert = plan.upserts.single()
        assertEquals(0L, upsert.id)
        assertEquals("c7", upsert.clientId)
        assertEquals(9L, upsert.remoteId)
        assertFalse(upsert.dirty)
    }

    @Test
    fun merge_matchesByRemoteId_whenClientIdMissing() {
        val plan = WrongSyncMerger.merge(
            listOf(local(clientId = null, remoteId = 9L, question = "本地题干")),
            listOf(remote(clientId = "c1"))
        )
        assertEquals(1L, plan.upserts.single().id)
    }

    // ------------------------------------------------------------ push

    @Test
    fun toPushItems_onlyDirtyOnes() {
        val items = WrongSyncMerger.toPushItems(
            listOf(
                local(id = 1, dirty = true),
                local(id = 2, dirty = false),
                local(id = 3, dirty = true, deleted = true)
            )
        )
        assertEquals(listOf("c1", "c1"), items.map { it.clientId })
        assertEquals(2, items.size)
        assertTrue("软删记录要一起上传，服务端才知道该删", items.last().deleted)
    }

    @Test
    fun toPushItems_skipsRowsWithoutClientId() {
        val items = WrongSyncMerger.toPushItems(listOf(local(clientId = null, dirty = true)))
        assertTrue("没有 clientId 上行会被服务端当成新记录，宁可不传", items.isEmpty())
    }

    @Test
    fun toPushItems_fallbackUpdatedAt_forLegacyRows() {
        val items = WrongSyncMerger.toPushItems(listOf(local(updatedAt = 0L, dirty = true)), now = 1234L)
        assertEquals(1234L, items.single().updatedAt)
    }

    @Test
    fun applyPushResult_clearsDirtyAndBackfillsRemoteId() {
        val plan = WrongSyncMerger.applyPushResult(
            listOf(local(dirty = true, updatedAt = 100L)),
            listOf(remote(updatedAt = 200L))
        )
        val upsert = plan.upserts.single()
        assertEquals(9L, upsert.remoteId)
        assertFalse(upsert.dirty)
        assertEquals(200L, upsert.updatedAt)
        assertTrue(plan.deleteIds.isEmpty())
    }

    @Test
    fun applyPushResult_softDeletedLocally_isPurgedFromDevice() {
        val plan = WrongSyncMerger.applyPushResult(
            listOf(local(dirty = true, deleted = true)),
            listOf(remote())
        )
        assertEquals(listOf(1L), plan.deleteIds)
        assertTrue(plan.upserts.isEmpty())
    }

    @Test
    fun applyPushResult_notEchoed_keepsDirty() {
        // 服务端没回吐这条：不能当成已同步，否则改动永远丢在半路上
        val plan = WrongSyncMerger.applyPushResult(listOf(local(dirty = true)), emptyList())
        assertTrue(plan.upserts.isEmpty())
        assertTrue(plan.deleteIds.isEmpty())
    }

    @Test
    fun ensureClientIds_backfillsLegacyRows() {
        val patched = WrongSyncMerger.ensureClientIds(
            listOf(local(id = 1, clientId = null), local(id = 2, clientId = "c2"))
        ) { "generated-1" }
        assertEquals(1, patched.size)
        assertEquals(1L, patched.single().id)
        assertEquals("generated-1", patched.single().clientId)
    }
}
