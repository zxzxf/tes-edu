package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName

/**
 * T-B5 知识图谱可视化：GET /learning/knowledge-graph 响应体。
 *
 * 一次拉取即可离线渲染：
 *  - nodes 带布局深度 depth、掌握度配色带 band、ZPD 高亮、题目数；
 *  - edges 为学习流向：from=前置、to=后置（先学 from 再学 to）；
 *  - summary 供顶部统计头。
 */
data class GraphResponse(
    @SerializedName("student_id") val studentId: String? = null,
    val subject: String? = null,
    @SerializedName("generated_at") val generatedAt: String? = null,
    val summary: GraphSummary? = null,
    val nodes: List<GraphNode> = emptyList(),
    val edges: List<GraphEdge> = emptyList(),
    val note: String? = null
)

data class GraphSummary(
    val total: Int = 0,
    val mastered: Int = 0,
    val weak: Int = 0,
    val medium: Int = 0,
    val untouched: Int = 0,
    @SerializedName("in_zpd") val inZpd: Int = 0,
    val edges: Int = 0,
    @SerializedName("max_depth") val maxDepth: Int = 0
)

data class GraphNode(
    val code: String,
    val title: String? = null,
    val chapter: String? = null,
    val section: String? = null,
    val subject: String? = null,
    /** core=核心考点 / std=标准考点（节点尺寸略作区分） */
    val importance: String? = null,
    /** 最长前置链深度，根=0；布局直接按此分层 */
    val depth: Int = 0,
    /** 衰减后掌握度 0~1；null=未学过 */
    val mastery: Double? = null,
    /** none / weak / medium / strong（与后端阈值对齐：<0.6 薄弱、>=0.8 已掌握） */
    val band: String = BAND_NONE,
    /** 是否在最近发展区（前置就绪、自身未掌握）——待学高亮 */
    val zpd: Boolean = false,
    @SerializedName("practice_count") val practiceCount: Int = 0,
    @SerializedName("correct_count") val correctCount: Int = 0,
    @SerializedName("wrong_count") val wrongCount: Int = 0,
    @SerializedName("last_practiced_at") val lastPracticedAt: String? = null,
    /** 该知识点下可练的题目数 */
    @SerializedName("question_count") val questionCount: Int = 0,
    /** 直接前置（学习流向上的上游） */
    val prereqs: List<String> = emptyList()
) {
    val isMastered: Boolean get() = band == BAND_STRONG

    companion object {
        const val BAND_NONE = "none"
        const val BAND_WEAK = "weak"
        const val BAND_MEDIUM = "medium"
        const val BAND_STRONG = "strong"
    }
}

data class GraphEdge(
    /** 前置知识点（先学） */
    val from: String,
    /** 后置知识点（后学） */
    val to: String
)
