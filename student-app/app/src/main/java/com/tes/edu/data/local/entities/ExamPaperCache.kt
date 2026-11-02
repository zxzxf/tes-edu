package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 作业卷题干缓存（按 assignmentId 一行）。
 *
 * 考试卷在**发布时即冻结快照**（`exam_paper_questions` 是快照，草稿另存），题干不会变，
 * 所以把 `GET /api/student/exams/{id}` 的整份结果按 assignmentId 缓存到本地是安全的：
 * 打开卷子时先出缓存秒开，再后台静默刷新。这样弱网下"转圈半天"的体感被消除。
 */
@Entity(tableName = "exam_paper_cache")
data class ExamPaperCache(
    @PrimaryKey
    @ColumnInfo(name = "assignment_id") val assignmentId: Long,
    @ColumnInfo(name = "paper_id") val paperId: Long,
    /** `List<ExamQuestionView>` 的 Gson JSON（Room 不存复杂对象，序列化落库）。 */
    @ColumnInfo(name = "questions_json") val questionsJson: String,
    @ColumnInfo(name = "updated_at") val updatedAt: Long
)
