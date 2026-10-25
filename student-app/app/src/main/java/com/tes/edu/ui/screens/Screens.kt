package com.tes.edu.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.background
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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AccountTree
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.AutoStories
import androidx.compose.material.icons.rounded.Bookmark
import androidx.compose.material.icons.rounded.Cancel
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.Lightbulb
import androidx.compose.material.icons.rounded.MenuBook
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.Quiz
import androidx.compose.material.icons.rounded.Send
import androidx.compose.material.icons.rounded.TipsAndUpdates
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.data.remote.Judgement
import com.tes.edu.ui.components.GradientButton
import com.tes.edu.ui.components.InfoPill
import com.tes.edu.ui.components.LabelValueRow
import com.tes.edu.ui.components.SoftCard
import com.tes.edu.ui.components.SoftFieldCard
import com.tes.edu.ui.components.SoftNotice
import com.tes.edu.ui.components.SoftOutlinedButton
import com.tes.edu.ui.components.SoftQuote
import com.tes.edu.ui.components.TesHero
import com.tes.edu.ui.components.EmptyState
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.viewmodel.SolveUiState
import com.tes.edu.viewmodel.SolveViewModel

// 六大能力页定义（底部导航）
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Input : Screen("input", "输入", Icons.Rounded.Edit)
    object Camera : Screen("camera", "拍照", Icons.Rounded.PhotoCamera)
    object Explain : Screen("explain", "讲解", Icons.Rounded.MenuBook)
    object Similar : Screen("similar", "触类", Icons.Rounded.AutoStories)
    object Graph : Screen("graph", "图谱", Icons.Rounded.AccountTree)
    object Review : Screen("review", "背诵", Icons.Rounded.Bookmark)
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

/**
 * S4 过程采集：题干 + 多行作答过程 -> 提交判分 -> 展示 judge v2 步骤级归因。
 * T-C2/C3：Hint 阶梯对话（开始引导/回应/等级与分步进度）+
 * "直接看解析"一等功能（常驻按钮 + 一次轻确认）。
 *
 * 视觉层：渐变头图建立重心 → 两块软输入卡 → 主/次 CTA →
 * 结果区（参考答案 / 判分卡 / 引导对话）按信息优先级逐层展开。
 */
@Composable
fun InputScreen(vm: SolveViewModel = viewModel()) {
    val ui by vm.ui.collectAsState()
    var question by remember { mutableStateOf("") }
    var process by remember { mutableStateOf("") }
    var reply by remember { mutableStateOf("") }
    // T-C3：直接看解析的一次轻确认弹层（常驻按钮点击后弹出，确认才直达 L4）
    var showDirectConfirm by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))

        TesHero(
            title = "解题 · 过程采集",
            subtitle = "写下题目，我陪你一步步想清楚",
            badge = "AI 苏格拉底引导"
        )

        Spacer(Modifier.height(18.dp))

        SoftFieldCard(
            label = "题目",
            hint = "粘贴或手打都行",
            value = question,
            onValueChange = { question = it },
            placeholder = "把题目放进来…",
            minLines = 3,
            leadingIcon = Icons.Rounded.Quiz
        )

        Spacer(Modifier.height(16.dp))

        SoftFieldCard(
            label = "我的作答",
            hint = "解答题请写出过程",
            value = process,
            onValueChange = { process = it },
            placeholder = "例如：由 A+B+C=π 且 A+B=3C，得 4C=π，所以 C=π/4…",
            minLines = 5,
            leadingIcon = Icons.Rounded.EditNote,
            accent = TesEduColors.Mint
        )

        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Rounded.Lightbulb,
                contentDescription = null,
                tint = TesEduColors.Amber,
                modifier = Modifier.size(14.dp)
            )
            Spacer(Modifier.width(5.dp))
            Text(
                "只写答案也可以，我会告诉你卡在哪一步",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        Spacer(Modifier.height(18.dp))

        GradientButton(
            text = if (process.isBlank()) "只求讲解" else "提交作答并判分",
            icon = Icons.Rounded.AutoAwesome,
            enabled = question.isNotBlank() && !ui.loading
        ) { vm.solve(question, process) }

        // T-C2：进入 Hint 阶梯对话（首轮 L0 点考点；阶梯只存在于引导档）
        if (!ui.hintActive) {
            Spacer(Modifier.height(10.dp))
            SoftOutlinedButton(
                text = "开始引导（一步一步来）",
                icon = Icons.Rounded.TipsAndUpdates,
                enabled = question.isNotBlank() && !ui.loading
            ) { vm.startHint(question) }
        }

        if (ui.loading) {
            Spacer(Modifier.height(16.dp))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically
            ) {
                CircularProgressIndicator(
                    Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = TesEduColors.Brand
                )
                Spacer(Modifier.width(10.dp))
                Text(
                    "正在思考…",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextSecondary
                )
            }
        }

        ui.error?.let { err ->
            Spacer(Modifier.height(16.dp))
            SoftNotice(
                text = err,
                icon = Icons.Rounded.ErrorOutline,
                color = TesEduColors.Danger,
                bg = TesEduColors.DangerSoft
            )
        }

        // T-C3：题型路由标签（记忆型直接讲解 / 中等引导 1-2 轮 / 压轴 3-4 轮分步）
        ui.questionTierName?.let { tier ->
            Spacer(Modifier.height(14.dp))
            TierRow(
                tierName = tier,
                questionType = ui.questionType,
                maxRounds = ui.hintMaxRounds
            )
        }

        if (ui.answer.isNotBlank()) {
            Spacer(Modifier.height(16.dp))
            AnswerCard(ui.answer)
        }

        ui.judgement?.let { j ->
            Spacer(Modifier.height(16.dp))
            JudgementCard(j)
        }

        // ---------------- T-C2/C3：Hint 阶梯对话区 ----------------
        if (ui.hintActive) {
            Spacer(Modifier.height(16.dp))
            HintPanel(
                ui = ui,
                reply = reply,
                onReplyChange = { reply = it },
                onReply = {
                    vm.replyHint(question, reply)
                    reply = ""
                },
                onRevealDirect = { showDirectConfirm = true }
            )
        }

        Spacer(Modifier.height(28.dp))
    }

    // T-C3：一次轻确认（不是反复挽留——点一次确认一次，尊重学生选择）
    if (showDirectConfirm) {
        AlertDialog(
            onDismissRequest = { showDirectConfirm = false },
            shape = RoundedCornerShape(22.dp),
            containerColor = TesEduColors.Surface,
            icon = {
                Box(
                    Modifier
                        .size(46.dp)
                        .clip(CircleShape)
                        .background(TesEduColors.BrandSoft),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Rounded.MenuBook,
                        contentDescription = null,
                        tint = TesEduColors.Brand,
                        modifier = Modifier.size(24.dp)
                    )
                }
            },
            title = {
                Text(
                    "直接看解析？",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = TesEduColors.TextPrimary
                )
            },
            text = {
                Text(
                    "将直接展示完整解析和复盘三问（这题考什么 / 卡在哪一步 / 同型题怎么识别），" +
                        "本题的引导对话随之结束。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextSecondary
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        showDirectConfirm = false
                        vm.revealDirect(question)
                    },
                    shape = RoundedCornerShape(12.dp)
                ) { Text("看解析") }
            },
            dismissButton = {
                TextButton(onClick = { showDirectConfirm = false }) { Text("继续引导") }
            }
        )
    }
}

/** 题型胶囊行：横向可滚动，避免窄屏挤压换行。 */
@Composable
private fun TierRow(tierName: String, questionType: String?, maxRounds: Int?) {
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        InfoPill("题型 · $tierName", TesEduColors.BrandDeep, TesEduColors.BrandSoft)
        if (questionType != null) {
            InfoPill(questionType, TesEduColors.TextSecondary, TesEduColors.FieldBg)
        }
        if (maxRounds != null) {
            InfoPill("引导上限 $maxRounds 轮", TesEduColors.Amber, TesEduColors.AmberSoft)
        }
    }
}

/** 参考答案卡：标题带图标，正文放大行距，便于阅读长解析。 */
@Composable
private fun AnswerCard(answer: String) {
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(26.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(TesEduColors.BrandSoft),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    Icons.Rounded.MenuBook,
                    contentDescription = null,
                    tint = TesEduColors.Brand,
                    modifier = Modifier.size(15.dp)
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                "参考答案",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
        }
        Spacer(Modifier.height(12.dp))
        Text(
            answer,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
    }
}

/**
 * 判分卡：头部用语义色 + 图标一眼定调（正确 / 错误 / 待补过程），
 * 明细字段走「标签-值」两列，成段文字（证据/点评/纠正）用浅底色块承托。
 */
@Composable
private fun JudgementCard(j: Judgement) {
    val accent: Color
    val soft: Color
    val title: String
    val icon: ImageVector
    val subtitle: String
    when {
        j.needProcess -> {
            accent = TesEduColors.Amber
            soft = TesEduColors.AmberSoft
            title = "还差解题过程"
            icon = Icons.Rounded.Info
            subtitle = "补上过程，我来按步骤归因"
        }
        j.correct == true -> {
            accent = TesEduColors.Success
            soft = TesEduColors.SuccessSoft
            title = "判分结果 · 正确"
            icon = Icons.Rounded.CheckCircle
            subtitle = "思路走通了，看看还能不能更快"
        }
        j.correct == false -> {
            accent = TesEduColors.Danger
            soft = TesEduColors.DangerSoft
            title = "判分结果 · 错误"
            icon = Icons.Rounded.Cancel
            subtitle = "别急，错误里藏着最值钱的线索"
        }
        else -> {
            accent = TesEduColors.TextMuted
            soft = TesEduColors.FieldBg
            title = "判分结果 · 待判"
            icon = Icons.Rounded.ErrorOutline
            subtitle = "暂未判分"
        }
    }

    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(soft),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    icon,
                    contentDescription = null,
                    tint = accent,
                    modifier = Modifier.size(22.dp)
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    color = accent,
                    fontWeight = FontWeight.Bold
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    subtitle,
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }

        // S4 提交协议：没贴过程 ≠ 做错，明确引导补过程
        if (j.needProcess) {
            Spacer(Modifier.height(12.dp))
            SoftNotice(
                text = j.comment?.takeIf { it.isNotBlank() }
                    ?: "需要先写出解题过程才能判分。",
                icon = Icons.Rounded.Info,
                color = TesEduColors.Amber,
                bg = TesEduColors.AmberSoft
            )
        } else {
            if (j.firstWrongStep != null) {
                Spacer(Modifier.height(12.dp))
                LabelValueRow("首个出错步骤", "第 ${j.firstWrongStep} 步")
            }
            j.errorType?.let { type ->
                Spacer(Modifier.height(8.dp))
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "错因类型",
                        style = MaterialTheme.typography.labelMedium,
                        color = TesEduColors.TextMuted,
                        modifier = Modifier.width(88.dp)
                    )
                    InfoPill(
                        ERROR_TYPE_LABEL[type] ?: type,
                        accent,
                        soft
                    )
                }
            }
            j.evidence?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(12.dp))
                SoftQuote("证据", it, accent, soft)
            }
            j.comment?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(10.dp))
                SoftQuote("点评", it, accent, soft)
            }
            j.correction?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(10.dp))
                SoftQuote("纠正", it, accent, soft)
            }
        }
    }
}

/**
 * 引导对话面板：等级胶囊 + 分步进度条 + 本步提示 + 回应输入 + 双 CTA。
 * 「直接看解析」保留为与主按钮并排的次按钮（一等功能，但不喧宾夺主）。
 */
@Composable
private fun HintPanel(
    ui: SolveUiState,
    reply: String,
    onReplyChange: (String) -> Unit,
    onReply: () -> Unit,
    onRevealDirect: () -> Unit
) {
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(30.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(TesEduColors.BrandSoft),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    Icons.Rounded.TipsAndUpdates,
                    contentDescription = null,
                    tint = TesEduColors.Brand,
                    modifier = Modifier.size(17.dp)
                )
            }
            Spacer(Modifier.width(10.dp))
            Text(
                "引导对话",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
            Spacer(Modifier.weight(1f))
            ui.hintLevel?.let { level ->
                val name = ui.hintLevelName?.let { " · $it" } ?: ""
                InfoPill("L$level$name", TesEduColors.BrandDeep, TesEduColors.BrandSoft)
            }
        }

        // 分步进度（压轴题）
        val step = ui.hintStep
        val total = ui.hintStepTotal
        if (step != null && total != null && total > 0) {
            Spacer(Modifier.height(12.dp))
            LinearProgressIndicator(
                progress = { step.toFloat() / total.toFloat() },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(7.dp)
                    .clip(RoundedCornerShape(50)),
                color = TesEduColors.Brand,
                trackColor = TesEduColors.BrandSoft
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "第 $step / $total 步",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        ui.hintStepText?.let { text ->
            Spacer(Modifier.height(12.dp))
            Column(
                Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(14.dp))
                    .background(TesEduColors.BrandSofter)
                    .padding(12.dp)
            ) {
                Text(
                    "本步提示",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.Brand,
                    fontWeight = FontWeight.SemiBold
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextPrimary
                )
            }
        }

        ui.hintRounds?.let { rounds ->
            Spacer(Modifier.height(8.dp))
            val cap = ui.hintMaxRounds?.let { " / 上限 $it 轮" } ?: ""
            Text(
                "第 $rounds 轮$cap",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        if (ui.hintFinale) {
            Spacer(Modifier.height(8.dp))
            InfoPill("压轴题：完整解析需主动查看", TesEduColors.Amber, TesEduColors.AmberSoft)
        }

        Spacer(Modifier.height(14.dp))
        SoftFieldCard(
            label = "我的回应",
            hint = "也可以说「我不懂」",
            value = reply,
            onValueChange = onReplyChange,
            placeholder = "说说这一步的思路…",
            minLines = 2,
            leadingIcon = Icons.Rounded.ChatBubbleOutline
        )

        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            GradientButton(
                text = "回应",
                icon = Icons.Rounded.Send,
                enabled = reply.isNotBlank() && !ui.loading,
                modifier = Modifier.weight(1f)
            ) { onReply() }

            // T-C3："直接看解析"一等功能——常驻按钮，一次轻确认后直达 L4
            if (ui.showDirectReveal) {
                SoftOutlinedButton(
                    text = "直接看解析",
                    icon = Icons.Rounded.MenuBook,
                    enabled = !ui.loading,
                    modifier = Modifier.weight(1f)
                ) { onRevealDirect() }
            }
        }

        if (ui.hintDirectReveal) {
            Spacer(Modifier.height(10.dp))
            Text(
                "已展示完整解析与复盘三问（主动查看解析不计入直给率）",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }
    }
}

@Composable
fun CameraScreen() = EmptyState(
    icon = Icons.Rounded.PhotoCamera,
    title = "拍照识题",
    subtitle = "拍下题目自动识别，直接进入引导"
)

@Composable
fun ExplainScreen() = EmptyState(
    icon = Icons.Rounded.MenuBook,
    title = "讲解展示",
    subtitle = "分步解析与公式推导将在这里呈现"
)

@Composable
fun SimilarScreen() = EmptyState(
    icon = Icons.Rounded.AutoStories,
    title = "触类旁通",
    subtitle = "做完一题，顺手串起同型题"
)

// GraphScreen：T-B5 知识图谱可视化已落地，见 GraphScreen.kt（Canvas 分层 DAG + 掌握度叠层 + ZPD）
@Composable
fun ReviewScreen() = EmptyState(
    icon = Icons.Rounded.Bookmark,
    title = "背诵卡 / 错题本",
    subtitle = "错题自动归档，离线也能背"
)
