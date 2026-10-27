package com.tes.edu.data.remote

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * `learning/` 端点 DTO 的契约测试：后端一律 snake_case，前端一律驼峰。
 *
 * 少写一个 @SerializedName 不会报错、只会静默拿到 null——这类问题在真机上
 * 表现为"同步了但列表是空的"，排查成本极高，所以用 Gson 直接钉死字段映射。
 */
class LearningModelsTest {

    @Test
    fun wrongRemote_parsesSnakeCase() {
        val json = """
            {
              "id": 12,
              "client_id": "c-1",
              "question": "求 f(x)=x^2 的最小值",
              "my_answer": "1",
              "correct_answer": "0",
              "knowledge_point": "MA-B4-C2-S1-K03",
              "error_type": "calc",
              "comment": "配方时符号错了",
              "correction": "(x-1)^2-1",
              "first_wrong_step": 2,
              "created_at": 1700000000000,
              "updated_at": 1700000005000,
              "deleted_at": null
            }
        """.trimIndent()
        val r = Gson().fromJson(json, WrongRemote::class.java)
        assertEquals(12L, r.id)
        assertEquals("c-1", r.clientId)
        assertEquals("求 f(x)=x^2 的最小值", r.question)
        assertEquals("1", r.myAnswer)
        assertEquals("0", r.correctAnswer)
        assertEquals("MA-B4-C2-S1-K03", r.knowledgePoint)
        assertEquals("calc", r.errorType)
        assertEquals("配方时符号错了", r.comment)
        assertEquals("(x-1)^2-1", r.correction)
        assertEquals(2, r.firstWrongStep)
        assertEquals(1700000000000L, r.createdAt)
        assertEquals(1700000005000L, r.updatedAt)
        assertNull(r.deletedAt)
    }

    @Test
    fun wrongSyncResponse_parsesServerTime() {
        val json = """
            { "items": [ { "id": 3, "question": "q", "deleted_at": 1700000009000 } ],
              "server_time": 1700000010000 }
        """.trimIndent()
        val res = Gson().fromJson(json, WrongSyncResponse::class.java)
        assertEquals(1700000010000L, res.serverTime)
        assertEquals(1, res.items.size)
        assertEquals(1700000009000L, res.items.single().deletedAt)
    }

    @Test
    fun wrongPushItem_serializesSnakeCase() {
        val item = WrongPushItem(
            clientId = "c-1",
            question = "q",
            errorType = "calc",
            firstWrongStep = 2,
            updatedAt = 1700000000000L,
            deleted = true
        )
        val json = Gson().toJson(item)
        assert(json.contains("\"client_id\":\"c-1\""))
        assert(json.contains("\"first_wrong_step\":2"))
        assert(json.contains("\"updated_at\":1700000000000"))
        assert(json.contains("\"deleted\":true"))
        val req = Gson().toJson(WrongSyncRequest("student_001", listOf(item)))
        assert(req.contains("\"student_id\":\"student_001\""))
    }

    @Test
    fun wrongStats_parsesMastery() {
        val json = """
            {
              "total": 12,
              "by_error_type": { "calc": 5, "read": 2 },
              "by_knowledge_point": { "MA-B4-C2-S1-K03": 4 },
              "mastery": [ { "kp": "MA-B4-C2-S1-K03", "mastery": 0.62, "delta": 0.1 } ]
            }
        """.trimIndent()
        val s = Gson().fromJson(json, WrongStats::class.java)
        assertEquals(12, s.total)
        assertEquals(5, s.byErrorType["calc"])
        assertEquals(2, s.byErrorType["read"])
        assertEquals(4, s.byKnowledgePoint["MA-B4-C2-S1-K03"])
        val m = s.mastery.single()
        assertEquals("MA-B4-C2-S1-K03", m.kp)
        assertEquals(0.62, m.mastery, 1e-9)
        assertEquals(0.1, m.delta, 1e-9)
    }

    @Test
    fun drillCardRemote_parsesSnakeCase() {
        val json = """
            {
              "id": 7, "client_id": "dc-1", "kp": "MA-B4-C2-S1-K03",
              "front": "正面", "back": "背面", "due_at": 1700000000000,
              "ease": 2.5, "interval_days": 6.0, "reps": 2, "lapses": 1, "deleted_at": null
            }
        """.trimIndent()
        val c = Gson().fromJson(json, DrillCardRemote::class.java)
        assertEquals(7L, c.id)
        assertEquals("dc-1", c.clientId)
        assertEquals("正面", c.front)
        assertEquals("背面", c.back)
        assertEquals(1700000000000L, c.dueAt)
        assertEquals(2.5, c.ease, 1e-9)
        assertEquals(6.0, c.intervalDays, 1e-9)
        assertEquals(2, c.reps)
        assertEquals(1, c.lapses)
        assertNull(c.deletedAt)
    }

    @Test
    fun drillCardsResponse_parsesCounts() {
        val json = """
            { "items": [], "due_count": 5, "total": 30 }
        """.trimIndent()
        val res = Gson().fromJson(json, DrillCardsResponse::class.java)
        assertEquals(5, res.dueCount)
        assertEquals(30, res.total)
    }

    @Test
    fun drillReviewResult_parsesSchedule() {
        val json = """
            { "id": 7, "due_at": 1700000864000, "interval_days": 1.0,
              "ease": 2.3, "reps": 3, "lapses": 2 }
        """.trimIndent()
        val r = Gson().fromJson(json, DrillReviewResult::class.java)
        assertEquals(7L, r.id)
        assertEquals(1700000864000L, r.dueAt)
        assertEquals(1.0, r.intervalDays, 1e-9)
        assertEquals(2.3, r.ease, 1e-9)
        assertEquals(3, r.reps)
        assertEquals(2, r.lapses)
    }

    @Test
    fun drillStats_parsesNewCount() {
        val json = """{ "total": 30, "due": 5, "new_count": 8, "retention": 0.83 }""".trimIndent()
        val s = Gson().fromJson(json, DrillStats::class.java)
        assertEquals(30, s.total)
        assertEquals(5, s.due)
        assertEquals(8, s.newCount)
        assertEquals(0.83, s.retention, 1e-9)
    }
}
