package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName

/**
 * 作业闭环（高三·数学卷）学生端契约，字段名对齐 web-backend `app/models/schemas.py`。
 *
 * 与解题链路的区别：这里是**整卷结构化作答**——答案随卷快照在服务端，
 * 提交后由服务端确定性判分（0 token，不调 LLM），App 只负责采集与展示。
 *
 * 可空性约定：服务端可能给 null 的字段一律声明成可空；`options` 服务端保证是
 * `[{label, content}]` 列表（不是 jsonb 原样），可以安全声明为强类型。
 */

/** `GET /api/student/exams` 一行：一份可做的卷。 */
data class StudentExamItem(
    @SerializedName("assignment_id") val assignmentId: Long,
    @SerializedName("paper_id") val paperId: Long,
    val title: String = "",
    @SerializedName("paper_type") val paperType: String = "",
    val grade: String = "",
    val subject: String = "",
    @SerializedName("question_count") val questionCount: Int = 0,
    @SerializedName("due_at") val dueAt: String? = null,
    val submitted: Boolean = false
) {
    /** 卷型中文名（服务端枚举 -> 展示文案）。 */
    val paperTypeLabel: String
        get() = when (paperType) {
            "pure_choice" -> "纯选择题"
            "pure_blank" -> "纯填空题"
            "mixed" -> "混合卷"
            else -> paperType.ifBlank { "试卷" }
        }
}

/** 选项：服务端 `_options_to_list` 的固定形状。 */
data class ExamOption(
    val label: String = "",
    val content: String = ""
)

/**
 * `GET /api/student/exams/{id}` 一题。
 *
 * ⚠️ 该接口**不下发答案**（`answer`/`analysis` 都不在返回体里），
 * 所以这里也没有对应字段——别在 DTO 里留一个恒为空的 answer 去误导调用方。
 */
data class ExamQuestionView(
    val seq: Int,
    val qtype: String = "",
    val multi: Boolean = false,
    val stem: String = "",
    val options: List<ExamOption> = emptyList(),
    val difficulty: Int? = null,
    @SerializedName("knowledge_points") val knowledgePoints: List<String> = emptyList()
) {
    val isChoice: Boolean get() = qtype == "choice"
    val isBlank: Boolean get() = qtype == "blank"
    val isSubjective: Boolean get() = qtype == "subjective"

    /** 题型中文名，UI 角标用。 */
    val qtypeLabel: String
        get() = when (qtype) {
            "choice" -> if (multi) "多选" else "单选"
            "blank" -> "填空"
            "subjective" -> "解答"
            else -> qtype
        }
}

/** 提交时逐题作答。 */
data class ExamSubmitItem(
    val seq: Int,
    @SerializedName("student_answer") val studentAnswer: String
)

/**
 * `POST /api/student/exams/{id}/submit` 请求体。
 *
 * `client_id` 是幂等键：同一份卷的重试必须复用同一个值，否则服务端会把
 * 两次提交当两件事（虽然当前服务端还按 (assignment, student) 兜了一层，
 * 但不该依赖那层兜底——补传队列一旦上来就会重复提交）。
 */
data class ExamSubmitRequest(
    @SerializedName("client_id") val clientId: String,
    val answers: List<ExamSubmitItem>
)

/** 结果页逐题。`status=manual_pending` 表示该题待人工批改（填空/解答）。 */
data class ExamResultItem(
    val seq: Int,
    val qtype: String = "",
    val multi: Boolean = false,
    @SerializedName("student_answer") val studentAnswer: String? = null,
    @SerializedName("correct_answer") val correctAnswer: String? = null,
    val correct: Boolean = false,
    val partial: Boolean = false,
    val status: String = "auto",
    val analysis: String? = null
) {
    val manualPending: Boolean get() = status == "manual_pending"

    /** 逐题结论：对 / 漏选 / 错 / 待人工。 */
    val verdictLabel: String
        get() = when {
            manualPending -> "待人工批改"
            correct -> "正确"
            partial -> "漏选"
            else -> "错误"
        }
}

/** `GET /api/student/exams/{id}/result`（以及 submit 的响应）。 */
data class ExamResultDto(
    @SerializedName("submission_id") val submissionId: Long = 0,
    @SerializedName("assignment_id") val assignmentId: Long = 0,
    @SerializedName("paper_id") val paperId: Long = 0,
    val title: String = "",
    // 服务端 score 是"正确题数"，不是百分制
    val score: Double = 0.0,
    @SerializedName("correct_count") val correctCount: Int = 0,
    @SerializedName("total_count") val totalCount: Int = 0,
    @SerializedName("auto_gradeable_count") val autoGradeableCount: Int = 0,
    @SerializedName("manual_pending_count") val manualPendingCount: Int = 0,
    val status: String = "graded",
    @SerializedName("submitted_at") val submittedAt: String? = null,
    val items: List<ExamResultItem> = emptyList()
) {
    /** 有题待人工时 status 才是 manual_pending，别只看 count。 */
    val hasManualPending: Boolean get() = manualPendingCount > 0

    /** 已判部分的正确率文案；没有可自动判的题时不编造百分比。 */
    val autoScoreText: String
        get() = if (autoGradeableCount > 0) "$correctCount / $autoGradeableCount" else "—"
}
