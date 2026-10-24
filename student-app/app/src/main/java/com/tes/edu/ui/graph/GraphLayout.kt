package com.tes.edu.ui.graph

import com.tes.edu.data.remote.GraphEdge
import com.tes.edu.data.remote.GraphNode
import kotlin.math.max

/**
 * 布局配置（单位 dp，图空间坐标）。
 *
 * 章节带布局：真实 DAG（数学 435 点 / 435 边）96% 的前置边在章内，
 * 按章分带 + 带内按学习深度排列，跨章边自然相连；
 * 若按纯深度分层会得到 183 层的"面条图"（576×43824），不可用。
 */
data class LayoutConfig(
    /** 章节带宽度（横向占位，节点居中，标签向右侧延伸） */
    val bandWidth: Float = 168f,
    /** 带内节点纵向间距 */
    val nodeGapY: Float = 88f,
    /** 章标题占位高度 */
    val bandHeaderHeight: Float = 72f,
    /** 画布内边距 */
    val padding: Float = 56f
)

/** 布局后的节点：图空间坐标 + 所属章节带序号。 */
data class LaidOutNode(
    val node: GraphNode,
    val x: Float,
    val y: Float,
    val band: Int
) {
    val code: String get() = node.code
}

/** 布局后的边（引用同一份 LaidOutNode，绘制时直接取坐标）。 */
data class LaidOutEdge(val from: LaidOutNode, val to: LaidOutNode)

/** 章节带信息（绘制带底纹 + 章标题用）。 */
data class BandInfo(
    val key: String,
    val label: String,
    val x: Float,
    val width: Float,
    val count: Int
)

/** 布局结果。 */
data class GraphLayout(
    val nodes: List<LaidOutNode>,
    val edges: List<LaidOutEdge>,
    val bands: List<BandInfo>,
    val width: Float,
    val height: Float
)

/**
 * 章节带布局（纯函数、无 Android 依赖，可 JVM 单测）：
 *  1. 分章：code 前缀（如 MA-B1-C1）即章；异常格式回退 chapter 字段；
 *  2. 章序：按章 key 字典序（= 课本 必修n-第m章 顺序，符合学生心智）；
 *  3. 章内排序：depth 升序 + 章内已放置前置的横坐标均值（barycenter）+
 *     code，形成清晰的纵向学习路径；
 *  4. 定位：章带横向等距铺开，带内节点纵向等距落布。
 */
object GraphLayoutCalc {

    fun layout(
        nodes: List<GraphNode>,
        edges: List<GraphEdge>,
        config: LayoutConfig = LayoutConfig()
    ): GraphLayout {
        if (nodes.isEmpty()) {
            return GraphLayout(emptyList(), emptyList(), emptyList(), 0f, 0f)
        }

        val byCode = nodes.associateBy { it.code }
        val validEdges = edges.filter { byCode.containsKey(it.from) && byCode.containsKey(it.to) }

        // 1) 分章
        val chapterOf: (GraphNode) -> String = { n ->
            val parts = n.code.split("-")
            if (parts.size >= 3) "${parts[0]}-${parts[1]}-${parts[2]}" else n.chapter ?: "其他"
        }
        val bands: Map<String, List<GraphNode>> = nodes.groupBy(chapterOf)

        // 2) 章序：按章 key 字典序（= 课本 必修n-第m章 顺序）
        val bandOrder = bands.entries.sortedBy { it.key }

        val laidOut = HashMap<String, LaidOutNode>(nodes.size)
        val bandInfos = ArrayList<BandInfo>(bandOrder.size)
        var maxRows = 1

        bandOrder.forEachIndexed { bandIndex, (key, bandNodes) ->
            // 3) 章内排序：depth -> barycenter（章内前置）-> code
            val ordered = bandNodes.sortedWith(
                compareBy(
                    { n -> max(0, n.depth) },
                    { n ->
                        val ps = n.prereqs.mapNotNull { laidOut[it]?.x }
                        if (ps.isEmpty()) Float.MAX_VALUE else ps.average().toFloat()
                    },
                    { it.code }
                )
            )
            maxRows = max(maxRows, ordered.size)
            val bandX = config.padding + bandIndex * config.bandWidth
            ordered.forEachIndexed { row, n ->
                laidOut[n.code] = LaidOutNode(
                    node = n,
                    x = bandX + config.bandWidth / 2f,
                    y = config.padding + config.bandHeaderHeight + row * config.nodeGapY,
                    band = bandIndex
                )
            }
            bandInfos += BandInfo(
                key = key,
                label = key,
                x = bandX,
                width = config.bandWidth,
                count = bandNodes.size
            )
        }

        val laidEdges = validEdges.mapNotNull { e ->
            val f = laidOut[e.from]
            val t = laidOut[e.to]
            if (f != null && t != null) LaidOutEdge(f, t) else null
        }
        val width = config.padding * 2 + bandOrder.size * config.bandWidth
        val height = config.padding * 2 + config.bandHeaderHeight +
            (maxRows - 1).coerceAtLeast(0) * config.nodeGapY
        return GraphLayout(
            nodes = laidOut.values.sortedWith(compareBy({ it.band }, { it.y })),
            edges = laidEdges,
            bands = bandInfos,
            width = width,
            height = height
        )
    }
}
