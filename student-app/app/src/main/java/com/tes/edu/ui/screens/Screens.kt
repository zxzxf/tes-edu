package com.tes.edu.ui.screens

import android.widget.Toast
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
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.Lightbulb
import androidx.compose.material.icons.rounded.MenuBook
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.Quiz
import androidx.compose.material.icons.rounded.Refresh
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.Judgement
import com.tes.edu.state.AppBridge
import com.tes.edu.util.MathText
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
import com.tes.edu.viewmodel.SimilarViewModel
import com.tes.edu.viewmodel.SolveUiState
import com.tes.edu.viewmodel.SolveViewModel
import com.tes.edu.viewmodel.WrongBookViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// 六大能力页定义（底部导航）
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Input : Screen("input", "输入", Icons.Rounded.Edit)
    object Camera : Screen("camera", "拍照", Icons.Rounded.PhotoCamera)
    object Explain : Screen("explain", "讲解", Icons.Rounded.MenuBook)
    object Similar : Screen("similar", "触类", Icons.Rounded.AutoStories)
    object Graph : Screen("graph", "图谱", Icons.Rounded.AccountTree)
    // 标签随内容走：这一页现在是错题本，就叫「错题」
    object Review : Screen("review", "错题", Icons.Rounded.Bookmark)
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
fun InputScreen(
    vm: SolveViewModel = viewModel(),
    wrongVm: WrongBookViewModel = viewModel()
) {
    val ui by vm.ui.collectAsState()
    var question by remember { mutableStateOf("") }
    var process by remember { mutableStateOf("") }
    var reply by remember { mutableStateOf("") }
    // T-C3：直接看解析的一次轻确认弹层（常驻按钮点击后弹出，确认才直达 L4）
    var showDirectConfirm by remember { mutableStateOf(false) }

    // T-S4：触类页「再练这道」/ 图谱「练这个知识点」投递过来的题干——
    // 进来即预填并清掉上一题的作答与结果，避免"新题旧答"混着提交。
    val pendingQuestion by AppBridge.pendingQuestion.collectAsState()
    LaunchedEffect(pendingQuestion) {
        val q = pendingQuestion
        if (!q.isNullOrBlank()) {
            question = q
            process = ""
            reply = ""
            vm.reset()
            AppBridge.consumeQuestion()
        }
    }

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
            hint = "次方写 e^x，分数写 1/2",
            value = question,
            onValueChange = { question = it },
            placeholder = "把题目放进来…",
            minLines = 3,
            leadingIcon = Icons.Rounded.Quiz,
            mathPreview = true
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
            accent = TesEduColors.Mint,
            mathPreview = true
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
        ) {
            // 记录来源题干：触类页据此自动生成变式题
            AppBridge.setLastQuestion(question)
            vm.solve(question, process)
        }

        // T-C2：进入 Hint 阶梯对话（首轮 L0 点考点；阶梯只存在于引导档）
        if (!ui.hintActive) {
            Spacer(Modifier.height(10.dp))
            SoftOutlinedButton(
                text = "开始引导（一步一步来）",
                icon = Icons.Rounded.TipsAndUpdates,
                enabled = question.isNotBlank() && !ui.loading
            ) {
                AppBridge.setLastQuestion(question)
                vm.startHint(question)
            }
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
            JudgementCard(
                j = j,
                question = question,
                myAnswer = process,
                correctAnswer = ui.answer,
                knowledgePoint = ui.knowledgePoints.firstOrNull() ?: "",
                vm = wrongVm
            )
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
        // 解析里常见 `e^x`、`x^2`、`\frac{1}{2}`，按公式渲染（上标/分数/π）
        MathText(
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
private fun JudgementCard(
    j: Judgement,
    question: String,
    myAnswer: String,
    correctAnswer: String,
    knowledgePoint: String,
    vm: WrongBookViewModel
) {
    val ctx = LocalContext.current
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

        // 错题本入口：做错 / 还没写过程（值得回头看）的才收，判对的不需要
        if (j.correct == false || j.needProcess) {
            Spacer(Modifier.height(14.dp))
            SoftOutlinedButton(
                text = "收进错题本",
                icon = Icons.Rounded.Bookmark
            ) {
                vm.save(
                    WrongQuestion(
                        question = question,
                        myAnswer = myAnswer,
                        correctAnswer = correctAnswer,
                        knowledgePoint = knowledgePoint,
                        errorType = j.errorType,
                        firstWrongStep = j.firstWrongStep,
                        comment = j.comment,
                        correction = j.correction
                    )
                ) { added ->
                    Toast.makeText(
                        ctx,
                        if (added) "已收进错题本，去「错题」页能翻到" else "这道题已经在错题本里了",
                        Toast.LENGTH_SHORT
                    ).show()
                }
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
                MathText(
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
            leadingIcon = Icons.Rounded.ChatBubbleOutline,
            mathPreview = true
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

// CameraScreen：T-P2~P5 已落地，见 CameraScreen.kt（系统相机 Intent + 压缩上传 + 可编辑识别结果）

@Composable
fun ExplainScreen() = EmptyState(
    icon = Icons.Rounded.MenuBook,
    title = "讲解展示",
    subtitle = "分步解析与公式推导将在这里呈现"
)

/**
 * 触类旁通（T-S3/T-S4）：一题做完 -> 3 道同型/变式题 -> 点「再练这道」回输入页。
 *
 * 来源题干两条路：① 输入页提交过题，进来即自动带出并自动生成；
 * ② 学生在本页直接写题干。识别到不是自己想要的题就改题干或「换一批」，
 * 不做"生成即练"——变式题由 LLM 生成，内容质量必须留人工判断的口子。
 */
@Composable
fun SimilarScreen(
    onNavigate: (String) -> Unit = {},
    vm: SimilarViewModel = viewModel()
) {
    val ui by vm.ui.collectAsState()
    val lastQuestion by AppBridge.lastQuestion.collectAsState()
    var question by remember { mutableStateOf("") }
    // 自动带出只做一次：学生之后手动改题干时不被旧值顶回去
    var autoFilled by remember { mutableStateOf(false) }

    LaunchedEffect(lastQuestion) {
        if (!autoFilled && lastQuestion.isNotBlank() && !ui.loaded) {
            question = lastQuestion
            autoFilled = true
            vm.load(lastQuestion)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))

        TesHero(
            title = "触类旁通",
            subtitle = "同型题串起来练，才算真的会",
            badge = "AI 变式生成"
        )

        Spacer(Modifier.height(18.dp))

        SoftFieldCard(
            label = "来源题目",
            hint = "默认取你最近做的一题",
            value = question,
            onValueChange = {
                question = it
                if (it.isBlank()) vm.clear()
            },
            placeholder = "把要延伸的题目放进来…",
            minLines = 3,
            leadingIcon = Icons.Rounded.Quiz,
            mathPreview = true
        )

        Spacer(Modifier.height(14.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            GradientButton(
                text = if (ui.loading) "生成中…" else "生成变式题",
                icon = Icons.Rounded.AutoAwesome,
                enabled = question.isNotBlank() && !ui.loading,
                modifier = Modifier.weight(1f)
            ) { vm.load(question) }

            if (ui.loaded) {
                SoftOutlinedButton(
                    text = "换一批",
                    icon = Icons.Rounded.Refresh,
                    enabled = !ui.loading,
                    modifier = Modifier.weight(1f)
                ) { vm.refresh() }
            }
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
                    "正在想同型题…",
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

        if (ui.knowledgePoints.isNotEmpty()) {
            Spacer(Modifier.height(14.dp))
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                ui.knowledgePoints.forEach { kp ->
                    InfoPill(kp, TesEduColors.BrandDeep, TesEduColors.BrandSoft)
                }
            }
        }

        if (ui.loaded && !ui.loading && ui.items.isEmpty() && ui.error == null) {
            Spacer(Modifier.height(16.dp))
            SoftNotice(
                text = "这次没生成出变式题，换个题干或再试一次",
                icon = Icons.Rounded.Info,
                color = TesEduColors.Amber,
                bg = TesEduColors.AmberSoft
            )
        }

        if (ui.degraded) {
            Spacer(Modifier.height(16.dp))
            SoftNotice(
                text = "AI 变式题暂时不可用（模型额度受限），下面是题库片段，" +
                    "不是完整题目，不能拿来直接练。",
                icon = Icons.Rounded.Info,
                color = TesEduColors.Amber,
                bg = TesEduColors.AmberSoft
            )
        }

        ui.items.forEachIndexed { i, item ->
            Spacer(Modifier.height(14.dp))
            VariantCard(
                index = i,
                text = item,
                // 降级片段不带完整题干，"再练这道"会把残句搬回输入页——禁用它
                practiceEnabled = !ui.degraded,
                onPractice = {
                    // 直接把题干搬到输入页，学生落地即可作答
                    AppBridge.postQuestion(item)
                    onNavigate(Screen.Input.route)
                }
            )
        }

        if (ui.items.isNotEmpty() && !ui.degraded) {
            Spacer(Modifier.height(14.dp))
            SoftNotice(
                text = "变式题由 AI 生成，偶尔会雷同或偏题；不对味就点「换一批」。",
                icon = Icons.Rounded.Info,
                color = TesEduColors.Amber,
                bg = TesEduColors.AmberSoft
            )
            Spacer(Modifier.height(10.dp))
            Text(
                "点「再练这道」会把题干带回输入页，原来的作答会被清空。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        } else if (ui.items.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text(
                "想练完整的同型题，可以去「图谱」选一个知识点点「练这个知识点」——那里取的是题库真题。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        Spacer(Modifier.height(28.dp))
    }
}

/** 变式题卡：序号角标 + 题干 + 「再练这道」（降级片段时禁用该按钮）。 */
@Composable
private fun VariantCard(
    index: Int,
    text: String,
    practiceEnabled: Boolean,
    onPractice: () -> Unit
) {
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(26.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(TesEduColors.BrandSoft),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    "${index + 1}",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold,
                    color = TesEduColors.Brand
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                "变式题 ${index + 1}",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
        }
        Spacer(Modifier.height(12.dp))
        MathText(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(14.dp))
        SoftOutlinedButton(
            text = if (practiceEnabled) "再练这道" else "片段不完整，暂不可练",
            icon = Icons.Rounded.EditNote,
            enabled = practiceEnabled
        ) { onPractice() }
    }
}

// GraphScreen：T-B5 知识图谱可视化已落地，见 GraphScreen.kt（Canvas 分层 DAG + 掌握度叠层 + ZPD）

/**
 * 错题本（T-R）：本地 Room 缓存，断网也能翻。
 *
 * 归档入口在输入页的判分卡上（做错 / 还没写过程才收，判对的不收）——
 * 不搞自动全量归档：那样随手一试的题也会进来，错题本很快就失去意义。
 *
 * 背诵卡本体属波 2/3（后端缺学生端点与 drill_cards 表）；这里仍把图谱
 * 「背这个知识点」的落点接住，读同一个 `AppBridge.pendingFocusKp`。
 */
@Composable
fun ReviewScreen(
    onNavigate: (String) -> Unit = {},
    vm: WrongBookViewModel = viewModel()
) {
    val items by vm.items.collectAsState()
    val focusKp by AppBridge.pendingFocusKp.collectAsState()
    val focusTitle by AppBridge.pendingFocusTitle.collectAsState()
    // 删除是破坏性操作，先问一句再删
    var pendingRemove by remember { mutableStateOf<WrongQuestion?>(null) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))
        TesHero(
            title = "错题本",
            subtitle = "错了的题收在这儿，回头再练一遍",
            badge = "本地离线 · ${items.size} 道"
        )

        if (focusKp != null) {
            Spacer(Modifier.height(14.dp))
            SoftCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Rounded.Bookmark,
                        contentDescription = null,
                        tint = TesEduColors.Brand,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(8.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            "已选定背诵范围",
                            style = MaterialTheme.typography.labelSmall,
                            color = TesEduColors.TextMuted
                        )
                        Text(
                            "${focusTitle ?: focusKp} · ${focusKp}",
                            style = MaterialTheme.typography.titleSmall,
                            color = TesEduColors.TextPrimary
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "背诵卡正在建设中，届时这里会按该知识点筛出待背卡片。",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }

        if (items.isEmpty()) {
            Spacer(Modifier.height(24.dp))
            EmptyState(
                icon = Icons.Rounded.Bookmark,
                title = "错题本还是空的",
                subtitle = "做题被判错时点「收进错题本」，就会存到这里"
            )
        } else {
            Spacer(Modifier.height(14.dp))
            Text(
                "最近错的排在最前面。点「重练这道」会把题干带回输入页。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
            items.forEach { q ->
                Spacer(Modifier.height(14.dp))
                WrongCard(
                    q = q,
                    onPractice = {
                        AppBridge.postQuestion(q.question)
                        onNavigate(Screen.Input.route)
                    },
                    onRemove = { pendingRemove = q }
                )
            }
            Spacer(Modifier.height(28.dp))
        }
    }

    pendingRemove?.let { q ->
        AlertDialog(
            onDismissRequest = { pendingRemove = null },
            shape = RoundedCornerShape(22.dp),
            containerColor = TesEduColors.Surface,
            title = {
                Text(
                    "移出错题本？",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = TesEduColors.TextPrimary
                )
            },
            text = {
                Text(
                    "「${q.question.take(40)}…」将从错题本里移除，本地记录一并删掉。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextSecondary
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    vm.remove(q)
                    pendingRemove = null
                }) { Text("移出", color = TesEduColors.Danger) }
            },
            dismissButton = {
                TextButton(onClick = { pendingRemove = null }) { Text("再留着") }
            }
        )
    }
}

/** 单条错题：错因 + 题干 + 我的作答 + 纠正，两个出口（重练 / 移出）。 */
@Composable
private fun WrongCard(
    q: WrongQuestion,
    onPractice: () -> Unit,
    onRemove: () -> Unit
) {
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(30.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(TesEduColors.DangerSoft),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    Icons.Rounded.Bookmark,
                    contentDescription = null,
                    tint = TesEduColors.Danger,
                    modifier = Modifier.size(16.dp)
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    "错题",
                    style = MaterialTheme.typography.titleSmall,
                    color = TesEduColors.TextPrimary
                )
                if (!q.knowledgePoint.isNullOrBlank()) {
                    Text(
                        q.knowledgePoint,
                        style = MaterialTheme.typography.labelSmall,
                        color = TesEduColors.TextMuted
                    )
                }
            }
            Text(
                remember(q.createdAt) {
                    SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(q.createdAt))
                },
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        Spacer(Modifier.height(12.dp))
        // 题干里常有 `e^x`、`x^2`，走公式渲染
        MathText(
            q.question,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )

        if (!q.errorType.isNullOrBlank()) {
            Spacer(Modifier.height(10.dp))
            InfoPill(
                "错因 · ${ERROR_TYPE_LABEL[q.errorType] ?: q.errorType}",
                TesEduColors.Danger,
                TesEduColors.DangerSoft
            )
            q.firstWrongStep?.let {
                Spacer(Modifier.height(6.dp))
                Text(
                    "首个出错步骤：第 $it 步",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }

        if (q.myAnswer.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            SoftQuote("我的作答", q.myAnswer, TesEduColors.Amber, TesEduColors.AmberSoft)
        }
        if (!q.correction.isNullOrBlank()) {
            Spacer(Modifier.height(8.dp))
            SoftQuote("纠正", q.correction!!, TesEduColors.Success, TesEduColors.SuccessSoft)
        } else if (q.correctAnswer.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            SoftQuote("参考答案", q.correctAnswer, TesEduColors.Success, TesEduColors.SuccessSoft)
        }

        Spacer(Modifier.height(14.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SoftOutlinedButton(
                text = "重练这道",
                icon = Icons.Rounded.EditNote,
                modifier = Modifier.weight(1f)
            ) { onPractice() }
            SoftOutlinedButton(
                text = "移出",
                icon = Icons.Rounded.Delete,
                modifier = Modifier.weight(1f)
            ) { onRemove() }
        }
    }
}
