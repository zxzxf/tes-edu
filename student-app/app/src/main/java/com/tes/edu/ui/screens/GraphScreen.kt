package com.tes.edu.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.ui.graph.GraphLayout
import com.tes.edu.ui.graph.LaidOutEdge
import com.tes.edu.ui.graph.LaidOutNode
import com.tes.edu.viewmodel.GraphViewModel
import kotlin.math.hypot

// ------------------------------------------------------------------ 配色（浅色主题，显式色值）

private val ColorMastered = Color(0xFF66BB6A)   // 已掌握 >=0.8
private val ColorMedium = Color(0xFFFFCA28)     // 巩固中 0.6~0.8
private val ColorWeak = Color(0xFFEF5350)       // 薄弱 <0.6
private val ColorUntouched = Color(0xFFBDBDBD)  // 未学过
private val ColorZpdRing = Color(0xFF1E88E5)    // ZPD 待学高亮环
private val ColorSelectedRing = Color(0xFF0D47A1)
private val ColorEdge = Color(0xFFB0BEC5)
private val ColorEdgeActive = Color(0xFF42A5F5)
private val ColorCanvasBg = Color(0xFFF7F9FB)
private val BandBgA = Color(0xFFEDF2F7)   // 章节带底纹（奇偶交替，便于区分）
private val BandBgB = Color(0xFFF7F9FB)

private const val MIN_SCALE = 0.12f
private const val MAX_SCALE = 2.5f

/**
 * T-B5 知识图谱可视化页。
 *
 * 一次拉取 DAG 快照（nodes+edges+summary）后本地分层布局：
 *  - 节点颜色=掌握度配色带（后端阈值同 T-B2），蓝环=ZPD 待学；
 *  - 手势：拖动平移、双指缩放（锚定手势中心）、点选节点；
 *  - 底部详情卡：掌握度/练习统计/可练题目数/前置链。
 */
@Composable
fun GraphScreen(vm: GraphViewModel = viewModel()) {
    val ui by vm.ui.collectAsState()

    var scale by remember { mutableFloatStateOf(0.6f) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }

    val layout = ui.layout
    val measurer = rememberTextMeasurer()

    // 初始视图：适应宽度（图谱通常又宽又扁）
    LaunchedEffect(layout, canvasSize) {
        if (layout != null && canvasSize.width > 0 && layout.width > 0f) {
            scale = (canvasSize.width / layout.width).coerceIn(MIN_SCALE, 1f)
            offset = Offset(
                x = 0f,
                y = (canvasSize.height * 0.5f - layout.height * scale * 0.5f)
                    .coerceAtMost(0f)
            )
        }
    }

    Column(Modifier.fillMaxSize()) {
        ui.graph?.summary?.let { s ->
            GraphSummaryBar(s.total, s.mastered, s.medium, s.weak, s.untouched, s.inZpd, s.edges)
        }
        GraphLegend()

        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(ColorCanvasBg)
                .onSizeChanged { canvasSize = it }
        ) {
            when {
                ui.loading -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    CircularProgressIndicator()
                }

                ui.error != null -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(ui.error!!, color = MaterialTheme.colorScheme.error)
                        Spacer(Modifier.height(8.dp))
                        Button(onClick = { vm.load() }) { Text("重试") }
                    }
                }

                layout == null || layout.nodes.isEmpty() -> Box(Modifier.fillMaxSize(), Alignment.Center) {
                    Text(ui.graph?.note ?: "暂无图谱数据", color = Color.Gray)
                }

                else -> {
                    GraphCanvas(
                        layout = layout,
                        selectedCode = ui.selected?.code,
                        scale = scale,
                        offset = offset,
                        measurer = measurer,
                        onTransform = { centroid, pan, zoom ->
                            val old = scale
                            val newScale = (old * zoom).coerceIn(MIN_SCALE, MAX_SCALE)
                            val f = newScale / old
                            // 以手势中心为锚缩放，再叠加平移
                            offset = Offset(
                                x = centroid.x - (centroid.x - offset.x) * f + pan.x,
                                y = centroid.y - (centroid.y - offset.y) * f + pan.y
                            )
                            scale = newScale
                        },
                        onTap = { screen ->
                            // 屏幕坐标 -> 图空间坐标（s = p * scale + offset 的逆变换）
                            val gx = (screen.x - offset.x) / scale
                            val gy = (screen.y - offset.y) / scale
                            val hit = layout.nodes.minByOrNull { n ->
                                hypot((n.x - gx).toDouble(), (n.y - gy).toDouble()).toFloat()
                            }
                            // 命中阈值 20 图空间单位（约等于 core 节点半径 + 容差）；再点已选中的节点取消选中
                            val reached = hit != null &&
                                hypot((hit.x - gx).toDouble(), (hit.y - gy).toDouble()).toFloat() <= 20f
                            vm.select(
                                if (reached && hit != null && hit.code != ui.selected?.code) {
                                    hit.node
                                } else {
                                    null
                                }
                            )
                        }
                    )

                    // 缩放控制（右下角）
                    Column(
                        Modifier
                            .align(Alignment.BottomEnd)
                            .padding(12.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        ZoomButton("＋") { scale = (scale * 1.4f).coerceAtMost(MAX_SCALE) }
                        Spacer(Modifier.height(6.dp))
                        ZoomButton("－") { scale = (scale / 1.4f).coerceAtLeast(MIN_SCALE) }
                        Spacer(Modifier.height(6.dp))
                        ZoomButton("⤢") {
                            if (canvasSize.width > 0 && layout.width > 0f) {
                                scale = (canvasSize.width / layout.width).coerceIn(MIN_SCALE, 1f)
                                offset = Offset.Zero
                            }
                        }
                    }

                    ui.selected?.let { node ->
                        NodeDetailCard(
                            node = node,
                            onClose = { vm.select(null) },
                            modifier = Modifier.align(Alignment.BottomCenter)
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ZoomButton(text: String, onClick: () -> Unit) {
    Surface(
        shape = CircleShape,
        shadowElevation = 3.dp,
        color = Color.White,
        modifier = Modifier.size(40.dp)
    ) {
        IconButton(onClick = onClick) {
            Text(text, fontWeight = FontWeight.Bold, color = Color(0xFF37474F))
        }
    }
}

/** 顶部统计头：共 N 点 · 已掌握/巩固/薄弱/未学 · ZPD 待学 · 边数。 */
@Composable
private fun GraphSummaryBar(
    total: Int, mastered: Int, medium: Int, weak: Int, untouched: Int, inZpd: Int, edges: Int
) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant) {
        Text(
            "共 $total 个知识点 · 已掌握 $mastered · 巩固中 $medium · 薄弱 $weak · " +
                "未学 $untouched · ZPD 待学 $inZpd · 前置边 $edges",
            style = MaterialTheme.typography.labelMedium,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}

/** 图例：配色带 + ZPD 环含义。 */
@Composable
private fun GraphLegend() {
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        LegendDot(ColorMastered, "已掌握")
        LegendDot(ColorMedium, "巩固中")
        LegendDot(ColorWeak, "薄弱")
        LegendDot(ColorUntouched, "未学")
        LegendDot(ColorZpdRing, "ZPD 待学", ring = true)
        Text(
            "提示：拖动平移 · 双指缩放 · 点节点看详情",
            style = MaterialTheme.typography.labelSmall,
            color = Color.Gray
        )
    }
}

@Composable
private fun LegendDot(color: Color, label: String, ring: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(if (ring) 13.dp else 10.dp)
                .background(if (ring) Color.White else color, CircleShape)
        ) {
            if (ring) {
                Canvas(Modifier.fillMaxSize()) {
                    drawCircle(color = color, style = Stroke(width = 3f))
                }
            }
        }
        Spacer(Modifier.width(4.dp))
        Text(label, style = MaterialTheme.typography.labelSmall)
    }
}

/** 图谱画布：变换手势 + 命中测试 + 绘制。 */
@Composable
private fun GraphCanvas(
    layout: GraphLayout,
    selectedCode: String?,
    scale: Float,
    offset: Offset,
    measurer: TextMeasurer,
    onTransform: (Offset, Offset, Float) -> Unit,
    onTap: (Offset) -> Unit
) {
    Canvas(
        Modifier
            .fillMaxSize()
            .pointerInput(Unit) {
                detectTransformGestures { centroid, pan, zoom, _ ->
                    onTransform(centroid, pan, zoom)
                }
            }
            .pointerInput(Unit) {
                detectTapGestures { onTap(it) }
            }
    ) {
        // 图空间可见右边界：标签越界时翻到节点左侧
        val viewRight = (size.width - offset.x) / scale
        withTransform({
            translate(offset.x, offset.y)
            scale(scale, scale, pivot = Offset.Zero)
        }) {
            // 章节带底纹 + 章标题（最底层）
            layout.bands.forEachIndexed { i, b ->
                drawRect(
                    color = if (i % 2 == 0) BandBgA else BandBgB,
                    topLeft = Offset(b.x, 0f),
                    size = Size(b.width, layout.height)
                )
                val head = measurer.measure(
                    b.label,
                    style = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Color(0xFF546E7A)),
                    maxLines = 1
                )
                drawText(
                    textMeasurer = measurer,
                    text = b.label,
                    topLeft = Offset(b.x + 8f, 20f),
                    style = TextStyle(fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Color(0xFF546E7A)),
                    maxLines = 1
                )
                drawText(
                    textMeasurer = measurer,
                    text = "${b.count} 点",
                    topLeft = Offset(b.x + 8f, 20f + head.size.height + 2f),
                    style = TextStyle(fontSize = 9.sp, color = Color(0xFF90A4AE)),
                    maxLines = 1
                )
            }
            // 边先画（在节点下层）
            layout.edges.forEach { e ->
                val active = selectedCode != null &&
                    (e.from.code == selectedCode || e.to.code == selectedCode)
                drawGraphEdge(e, if (active) ColorEdgeActive else ColorEdge, if (active) 2.6f else 1.4f)
            }
            // 节点 + 标签
            layout.nodes.forEach { n ->
                drawGraphNode(
                    n,
                    selected = n.code == selectedCode,
                    showLabel = scale >= 0.55f,
                    measurer = measurer,
                    viewRight = viewRight
                )
            }
        }
    }
}

// ------------------------------------------------------------------ 绘制原语（DrawScope，图空间坐标）

private fun DrawScope.drawGraphEdge(e: LaidOutEdge, color: Color, width: Float) {
    drawLine(
        color = color,
        start = Offset(e.from.x, e.from.y),
        end = Offset(e.to.x, e.to.y),
        strokeWidth = width
    )
}

private fun DrawScope.drawGraphNode(
    n: LaidOutNode,
    selected: Boolean,
    showLabel: Boolean,
    measurer: TextMeasurer,
    viewRight: Float
) {
    val node = n.node
    val radius = if (node.importance == "core") 13f else 9.5f
    val fill = when (node.band) {
        GraphNode.BAND_STRONG -> ColorMastered
        GraphNode.BAND_MEDIUM -> ColorMedium
        GraphNode.BAND_WEAK -> ColorWeak
        else -> ColorUntouched
    }
    // 主圆
    drawCircle(color = fill, radius = radius, center = Offset(n.x, n.y))
    drawCircle(color = Color.White, radius = radius, center = Offset(n.x, n.y), style = Stroke(width = 1.6f))
    // ZPD 高亮环（待学）
    if (node.zpd) {
        drawCircle(
            color = ColorZpdRing,
            radius = radius + 4.5f,
            center = Offset(n.x, n.y),
            style = Stroke(width = if (selected) 3.6f else 2.4f)
        )
    }
    // 选中环
    if (selected) {
        drawCircle(
            color = ColorSelectedRing,
            radius = radius + 9f,
            center = Offset(n.x, n.y),
            style = Stroke(width = 3.2f)
        )
    }
    // 标签：缩放足够大时显示标题，超出可见右边界时翻到点左侧
    if (showLabel) {
        val title = node.title ?: node.code
        val result = measurer.measure(
            title,
            style = TextStyle(fontSize = 10.sp, color = Color(0xFF37474F)),
            maxLines = 1
        )
        val tw = result.size.width.toFloat()
        val th = result.size.height.toFloat()
        var tx = n.x + radius + 6f
        if (tx + tw > viewRight) tx = n.x - radius - 6f - tw
        drawText(
            textMeasurer = measurer,
            text = title,
            topLeft = Offset(tx, n.y - th / 2f),
            style = TextStyle(fontSize = 10.sp, color = Color(0xFF37474F)),
            maxLines = 1
        )
    }
}

/** 选中节点详情卡：掌握度 / 练习统计 / 可练题数 / 前置链。 */
@Composable
private fun NodeDetailCard(node: GraphNode, onClose: () -> Unit, modifier: Modifier = Modifier) {
    val bandLabel = when (node.band) {
        GraphNode.BAND_STRONG -> "已掌握"
        GraphNode.BAND_MEDIUM -> "巩固中"
        GraphNode.BAND_WEAK -> "薄弱"
        else -> "未学过"
    }
    val bandColor = when (node.band) {
        GraphNode.BAND_STRONG -> ColorMastered
        GraphNode.BAND_MEDIUM -> ColorMedium
        GraphNode.BAND_WEAK -> ColorWeak
        else -> ColorUntouched
    }
    Card(
        modifier
            .fillMaxWidth()
            .padding(12.dp),
        shape = RoundedCornerShape(16.dp)
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    node.title ?: node.code,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                if (node.zpd) {
                    AssistChip(
                        onClick = {},
                        label = { Text("ZPD 待学", fontSize = 11.sp) }
                    )
                    Spacer(Modifier.width(6.dp))
                }
                TextButton(onClick = onClose) { Text("关闭") }
            }
            Text(
                "${node.chapter.orEmpty()} / ${node.section.orEmpty()} · ${node.code}",
                style = MaterialTheme.typography.labelSmall,
                color = Color.Gray,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            // 掌握度
            if (node.mastery != null) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("掌握度", style = MaterialTheme.typography.labelMedium)
                    Spacer(Modifier.width(8.dp))
                    LinearProgressIndicator(
                        progress = { node.mastery.toFloat() },
                        modifier = Modifier
                            .weight(1f)
                            .height(8.dp),
                        color = bandColor,
                        trackColor = Color(0xFFECEFF1)
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "${(node.mastery * 100).toInt()}% · $bandLabel",
                        style = MaterialTheme.typography.labelMedium
                    )
                }
            } else {
                Text("掌握度：未学过（$bandLabel）", style = MaterialTheme.typography.labelMedium)
            }
            Text(
                "练习 ${node.practiceCount} 次 · 对 ${node.correctCount} · 错 ${node.wrongCount} · " +
                    "可练题目 ${node.questionCount} 道",
                style = MaterialTheme.typography.bodySmall
            )
            if (node.prereqs.isNotEmpty()) {
                Text(
                    "前置：${node.prereqs.joinToString("、")}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Color.Gray,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }
    }
}
