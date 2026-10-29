package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 作业卷作答草稿（断网也能答，联网才提交）。
 *
 * 一份卷一行，按 `assignmentId` 覆盖：学生边答边落盘，切页/杀进程/
 * 断网都不丢；提交成功后删行。
 *
 * 字段设计：
 *  - `answers`：`{"1":"A","2":"ACD","3":"3x+4y-5=0"}` 的 JSON 串。
 *    不拆成"一题一行"是因为草稿只整体读写、从不按题查询，
 *    一张表一行反而少写一半代码、少一处 schema 漂移风险。
 *  - `clientId`：提交幂等键。**随草稿一起持久化**——重试必须复用同一个值，
 *    每次提交新生成一个就等于放弃了幂等（补传队列上线后会重复交卷）。
 */
@Entity(tableName = "exam_drafts")
data class ExamDraft(
    @PrimaryKey @ColumnInfo(name = "assignment_id") val assignmentId: Long,
    @ColumnInfo(name = "paper_id") val paperId: Long = 0,
    @ColumnInfo(name = "answers") val answers: String = "{}",
    @ColumnInfo(name = "client_id") val clientId: String,
    @ColumnInfo(name = "updated_at") val updatedAt: Long = 0
)
