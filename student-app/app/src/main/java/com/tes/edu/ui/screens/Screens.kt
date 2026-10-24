package com.tes.edu.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountTree
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MenuBook
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.collectAsState
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.data.remote.Judgement
import com.tes.edu.viewmodel.SolveViewModel

// 六大能力页定义（P0 基础壳：底部导航 + 占位页，后续填充真实交互）
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Input : Screen("input", "输入", Icons.Filled.Edit)
    object Camera : Screen("camera", "拍照", Icons.Filled.PhotoCamera)
    object Explain : Screen("explain", "讲解", Icons.Filled.MenuBook)
    object Similar : Screen("similar", "触类", Icons.Filled.AutoStories)
    object Graph : Screen("graph", "图谱", Icons.Filled.AccountTree)
    object Review : Screen("review", "背诵", Icons.Filled.Bookmark)
}

// 错因七类的中文展示名（后端落库为英文 taxonomy）
private val ERROR_TYPE_LABEL = mapOf(
    "concept" to "概念理解",
    "formula" to "公式误用",
    "case" to "分类讨论遗漏",
    "calc" to "计算运算",
    "read" to "审题漏条件",
    "logic" to "逻辑跳步",
    "strategy" to "方法策略",
    "unknown" to "待人工复核"
)

/** S4 过程采集：题干 + 多行作答过程 -> 提交判分 -> 展示 judge v2 步骤级归因。 */
@Composable
fun InputScreen(vm: SolveViewModel = viewModel()) {
    val ui by vm.ui.collectAsState()
    var question by remember { mutableStateOf("") }
    var process by remember { mutableStateOf("") }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("解题 · 过程采集", style = MaterialTheme.typography.titleLarge)

        OutlinedTextField(
            value = question,
            onValueChange = { question = it },
            label = { Text("题目") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 2
        )

        // 多行作答：解答题要求写出过程，一行行提交后才做步骤级归因
        OutlinedTextField(
            value = process,
            onValueChange = { process = it },
            label = { Text("我的作答（解答题请写出过程）") },
            placeholder = { Text("例如：由 A+B+C=π 且 A+B=3C，得 4C=π，所以 C=π/4…") },
            modifier = Modifier
                .fillMaxWidth()
                .height(160.dp),
            minLines = 5
        )

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(
                onClick = { vm.solve(question, process) },
                enabled = question.isNotBlank() && !ui.loading
            ) { Text(if (process.isBlank()) "只求讲解" else "提交作答并判分") }
        }

        if (ui.loading) {
            CircularProgressIndicator(Modifier.align(Alignment.CenterHorizontally))
        }
        ui.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

        if (ui.answer.isNotBlank()) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("参考答案", fontWeight = FontWeight.Bold)
                    Text(ui.answer, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        ui.judgement?.let { JudgementCard(it) }
    }
}

@Composable
private fun JudgementCard(j: Judgement) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("判分结果", fontWeight = FontWeight.Bold)
            // S4 提交协议：没贴过程 ≠ 做错，明确引导补过程
            if (j.needProcess) {
                Text(
                    "需要先写出解题过程才能判分：${j.comment.orEmpty()}",
                    color = MaterialTheme.colorScheme.error
                )
                return@Column
            }
            Text(
                when (j.correct) {
                    true -> "✅ 正确"
                    false -> "❌ 错误"
                    null -> "— 未判分"
                }
            )
            j.firstWrongStep?.let { Text("首个出错步骤：第 $it 步") }
            j.errorType?.let {
                Text("错因类型：${ERROR_TYPE_LABEL[it] ?: it}（$it）")
            }
            j.evidence?.takeIf { it.isNotBlank() }?.let { Text("证据：$it") }
            j.comment?.takeIf { it.isNotBlank() }?.let { Text("点评：$it") }
            j.correction?.takeIf { it.isNotBlank() }?.let { Text("纠正：$it") }
        }
    }
}

@Composable fun CameraScreen() = Placeholder("拍照识题（走 /solve_image OCR 链路）")
@Composable fun ExplainScreen() = Placeholder("讲解展示（公式渲染）")
@Composable fun SimilarScreen() = Placeholder("触类旁通")
@Composable fun GraphScreen() = Placeholder("知识图谱可视化")
@Composable fun ReviewScreen() = Placeholder("背诵卡 / 错题本（离线）")

@Composable
private fun Placeholder(title: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text("P0 占位页，待 P1 填充", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp))
        }
    }
}
