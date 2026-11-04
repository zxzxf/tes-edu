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

/** M3-1 解析响应：`solution_steps` 是 `{answer, steps[]}`，服务端缓存后原样返回。 */
data class ExamSolutionDto(
    @SerializedName("paper_id") val paperId: Long = 0,
    /** 卷内题 id（不是 seq），前端只做展示不做寻址。 */
    @SerializedName("question_id") val questionId: Long = 0,
    @SerializedName("solution_steps") val solutionSteps: ExamSolutionSteps? = null,
    /** true = 读的缓存（没有重复烧 token）。 */
    val cached: Boolean = false,
    /** false = 生成失败/被数值护栏丢弃，此时 `reason` 有说明。 */
    val ok: Boolean = true,
    val reason: String = ""
)

data class ExamSolutionSteps(
    val answer: String = "",
    val steps: List<String> = emptyList()
)

/** M3-4 按卷图谱：节点集合是卷内知识点 + 前驱 + 后继。 */
data class PaperGraphDto(
    @SerializedName("paper_id") val paperId: Long = 0,
    val summary: GraphSummary? = null,
    val nodes: List<GraphNode> = emptyList(),
    val edges: List<GraphEdge> = emptyList(),
    @SerializedName("paper_kp_count") val paperKpCount: Int = 0,
    @SerializedName("unresolved_kps") val unresolvedKps: List<String> = emptyList(),
    val note: String? = null
) {
    /**
     * 转成图谱页既有的响应形状，复用同一套布局/渲染。
     *
     * 按卷接口与全量图谱同构（nodes/edges/summary），不转的话图谱页要么复制一份
     * 渲染代码、要么在多处做分支——两者都会随时间漂移。
     */
    fun toGraphResponse(): GraphResponse = GraphResponse(
        subject = null,
        summary = summary,
        nodes = nodes,
        edges = edges,
        note = note?.takeIf { it.isNotBlank() }
            ?: if (paperKpCount == 0) "本卷题目尚未绑定知识点" else null
    )
}

/**
 * M3-6 结果页**单题**知识子图：`GET /api/student/exams/{id}/questions/{seq}/graph`。
 *
 * 与 [PaperGraphDto]（整卷）的区别：节点集合只围绕这一道题——本题考点 + 一层
 * 前驱 + 一层后继，学生看「查看结果」时能直接定位「这题卡在哪个知识点」，
 * 而不是被整卷十道题的点淹没。
 *
 * `core_kps/prereq_kps/successor_kps` 是互斥的三层 code 列表；[nodes] 是并集，
 * 用 code 回查即可，客户端不自己算 DAG（同一份口径只在服务端维护）。
 */
data class QuestionGraphDto(
    @SerializedName("assignment_id") val assignmentId: Long = 0,
    @SerializedName("paper_id") val paperId: Long = 0,
    val seq: Int = 0,
    @SerializedName("question_id") val questionId: Long? = null,
    @SerializedName("core_kps") val coreKps: List<String> = emptyList(),
    @SerializedName("prereq_kps") val prereqKps: List<String> = emptyList(),
    @SerializedName("successor_kps") val successorKps: List<String> = emptyList(),
    val nodes: List<GraphNode> = emptyList(),
    val edges: List<GraphEdge> = emptyList(),
    @SerializedName("unresolved_kps") val unresolvedKps: List<String> = emptyList(),
    val note: String? = null
) {
    private fun nodesOf(codes: List<String>): List<GraphNode> =
        codes.mapNotNull { code -> nodes.firstOrNull { it.code == code } }

    /** 本题考点（结果页主卡片用）。 */
    fun coreNodes(): List<GraphNode> = nodesOf(coreKps)

    /** 先学：这些点没过关，本题才做不出来。 */
    fun prereqNodes(): List<GraphNode> = nodesOf(prereqKps)

    /** 可延伸：本题之后能接着学的点。 */
    fun successorNodes(): List<GraphNode> = nodesOf(successorKps)

    val hasGraph: Boolean get() = nodes.isNotEmpty()
}

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
    @SerializedName("knowledge_points") val knowledgePoints: List<String> = emptyList(),
    /** 填空题空数（服务端按标答结构下发，只给空数不给答案）。多空题需逐空输入。 */
    @SerializedName("blank_count") val blankCount: Int = 0
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

    /** 是否多空填空（需要逐空输入）。 */
    val isMultiBlank: Boolean get() = isBlank && blankCount > 1

    /**
     * 把存储串切成逐空答案；返回 null 表示交给服务端切分。
     *
     * UI 逐空输入时各空用全角「；」连接（学生不需要知道分隔符），这里按它切开并
     * **补齐到 blankCount**：学生只填第 1 空时，缺的位留空串，服务端才能判
     * 「第 2 空错」；否则空段被服务端过滤掉，会变成「空数不符 → 转人工」，
     * 学生明明漏答却被挂成待批改。
     *
     * 只有一段说明是老版本单串作答（可能用 `,` 分隔），不在此切分——
     * 交给服务端 `split_blanks` 做括号感知切分（`[2,3)` 的逗号不能切）。
     */
    fun blankParts(answer: String): List<String>? {
        if (!isMultiBlank) return null
        val parts = answer.split('；').map { it.trim() }.toMutableList()
        if (parts.size <= 1) return null
        while (parts.size < blankCount) parts.add("")
        return parts.take(blankCount)
    }
}

/** 提交时逐题作答。 */
data class ExamSubmitItem(
    val seq: Int,
    @SerializedName("student_answer") val studentAnswer: String,
    /**
     * 多空填空逐空答案（按空序号，**允许空串占位**）。
     *
     * 传了就优先于服务端按分隔符切分 `student_answer`——学生漏答某一空时，
     * 空串占位能保留"第 2 空没写"这个事实，服务端据此判错；
     * 若靠分隔符切分，空段会被过滤掉、变成「空数不符 → 转人工」，
     * 学生明明漏答却被挂成待批改。
     */
    val blanks: List<String>? = null
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
    /** 转人工原因（「含无法确定性判定的空」等）。null 表示自动判分。 */
    val reason: String? = null,
    val analysis: String? = null,
    /** 老师判定时写的评语（manual_reviewed 才有）。 */
    @SerializedName("teacher_comment") val teacherComment: String? = null
) {
    val manualPending: Boolean get() = status == "manual_pending"

    /** 老师已人工判定：既不是机器 auto，也不是待人工。 */
    val reviewed: Boolean get() = status == "manual_reviewed"

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
    /** 其中已由老师复核判定的题数（含改判）。 */
    @SerializedName("manual_reviewed_count") val manualReviewedCount: Int = 0,
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

/**
 * 进卷合并接口 `GET /api/student/exams/{id}/bundle` 的响应。
 *
 * 服务端一次给全题干 + 是否已交 + 判分结果，App 进卷只发一个请求（跨境隧道
 * 每个请求实测 1.5–2.9s，少一次往返就是少一次转圈）。`result` 仅在本人已提交
 * 时返回；题干永不含答案。
 */
data class ExamBundleDto(
    @SerializedName("assignment_id") val assignmentId: Long = 0,
    @SerializedName("paper_id") val paperId: Long = 0,
    val title: String = "",
    @SerializedName("paper_type") val paperType: String = "",
    val submitted: Boolean = false,
    val questions: List<ExamQuestionView> = emptyList(),
    val result: ExamResultDto? = null
)
