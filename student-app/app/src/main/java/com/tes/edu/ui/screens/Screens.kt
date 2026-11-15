package com.tes.edu.ui.screens

import android.widget.Toast
import android.widget.VideoView
import android.widget.MediaController
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.ui.semantics.Role
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
import androidx.compose.material.icons.rounded.Assignment
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.AutoStories
import androidx.compose.material.icons.rounded.Bookmark
import androidx.compose.material.icons.rounded.Cancel
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.CloudOff
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.Edit
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.FormatListNumbered
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.Lightbulb
import androidx.compose.material.icons.rounded.MenuBook
import androidx.compose.material.icons.rounded.PhotoCamera
import androidx.compose.material.icons.rounded.Quiz
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Send
import androidx.compose.material.icons.rounded.TipsAndUpdates
import androidx.compose.material.icons.rounded.VolumeUp
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Close
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.DisposableEffect
import kotlinx.coroutines.launch
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.local.entities.WrongQuestion
import com.tes.edu.data.remote.ExamOption
import com.tes.edu.data.remote.Judgement
import com.tes.edu.data.remote.MasteryPoint
import com.tes.edu.data.remote.WrongRedoResult
import com.tes.edu.data.remote.WrongStats
import com.tes.edu.data.sync.redoFeedback
import com.tes.edu.data.sync.splitRedoBlanks
import com.tes.edu.state.AppBridge
import com.tes.edu.state.KpNameCache
import com.tes.edu.util.MathText
import com.tes.edu.util.TtsController
import com.tes.edu.util.toSpeechText
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
import com.tes.edu.viewmodel.DrillViewModel
import com.tes.edu.viewmodel.SimilarViewModel
import com.tes.edu.viewmodel.SolveUiState
import com.tes.edu.viewmodel.SolveViewModel
import com.tes.edu.viewmodel.WrongBookViewModel
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.downloadExplanationVideo
import com.tes.edu.data.remote.ExplanationVideoItem
import com.tes.edu.data.remote.listExplanationVideos
import com.tes.edu.data.remote.ensurePlayableVideoId
import com.tes.edu.viewmodel.WrongFilter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// 七个能力页（底部导航）：作业闭环 + 解题六页
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    // 作业闭环（M1-6）：老师下发的卷子在这里作答，是这一版的主入口
    object Exam : Screen("exam", "作业", Icons.Rounded.Assignment)
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

/** 题型标签：choice/blank/subjective -> 中文（错题本按题型筛选）。 */
private val QTYPE_LABEL = mapOf(
    "choice" to "选择题",
    "blank" to "填空题",
    "subjective" to "解答题"
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
    // 来自错题本「练习」的选择题选项（只读展示，JSON 串）；非选择题为空。
    var practiceOptionsJson by remember { mutableStateOf<String?>(null) }
    // 选择题在练习页直接点选答案（"A"/"B"...），替代「我的作答」文本输入。
    var selectedOption by remember { mutableStateOf<String?>(null) }

    // T-S4：触类页「再练这道」/ 图谱「练这个知识点」投递过来的题干——
    // 进来即预填并清掉上一题的作答与结果，避免"新题旧答"混着提交。
    val pendingQuestion by AppBridge.pendingQuestion.collectAsState()
    LaunchedEffect(pendingQuestion) {
        val p = pendingQuestion
        if (p != null && p.text.isNotBlank()) {
            question = p.text
            practiceOptionsJson = p.optionsJson
            selectedOption = null
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
            hint = "次方写 e^x，分数写 1/2，分母含多项必须加括号",
            value = question,
            onValueChange = { question = it },
            placeholder = "把题目放进来…",
            minLines = 3,
            leadingIcon = Icons.Rounded.Quiz,
            mathPreview = true
        )

        Spacer(Modifier.height(16.dp))

        // 选择题（来自错题本「练习」）把 A/B/C/D 列出：可直接点选答案，
        // 代替「我的作答」文本输入；填空/解答题无选项数据，自动不显示。
        val practiceOpts = remember(practiceOptionsJson) { parseWrongOptions(practiceOptionsJson) }
        val isChoice = practiceOpts.isNotEmpty()
        if (isChoice) {
            Column(Modifier.fillMaxWidth()) {
                Text(
                    "请选择答案",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = TesEduColors.TextSecondary
                )
                Spacer(Modifier.height(8.dp))
                Column(
                    Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    practiceOpts.forEach { opt ->
                        WrongOptionRow(
                            label = opt.label,
                            content = opt.content,
                            selected = selectedOption == opt.label,
                            onClick = { selectedOption = opt.label }
                        )
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
        }

        // 非选择题才需要手写作答（填空/解答/自由输入）；选择题走点选。
        if (!isChoice) {
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
        }

        Spacer(Modifier.height(18.dp))

        // 选择题：选中的选项即答案，提交即判分；非选择题：照旧文本作答。
        val answerForSolve = if (isChoice) selectedOption else process
        GradientButton(
            text = if (isChoice) {
                if (selectedOption != null) "提交判分" else "请选择答案"
            } else {
                if (process.isBlank()) "只求讲解" else "提交作答并判分"
            },
            icon = Icons.Rounded.AutoAwesome,
            enabled = if (isChoice) selectedOption != null && !ui.loading
            else question.isNotBlank() && !ui.loading
        ) {
            // 记录来源题干：触类页据此自动生成变式题
            AppBridge.setLastQuestion(question)
            vm.solve(question, answerForSolve)
        }

        // T-C2：进入 Hint 阶梯对话（首轮 L0 点考点；阶梯只存在于引导档）。
        // 选择题靠点选作答，引导流程不适用，故仅非选择题展示。
        if (!ui.hintActive && !isChoice) {
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

        // 解题步骤：引导档的第一步提示、解析档的分步推导都落在这里
        // （后端把引导内容放在 steps 而非 answer，不展示等于学生看不到引导）
        if (ui.visibleSteps.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            StepsCard(ui.visibleSteps)
        }

        ui.judgement?.let { j ->
            Spacer(Modifier.height(16.dp))
            JudgementCard(
                j = j,
                question = question,
                myAnswer = answerForSolve ?: process,
                correctAnswer = ui.answer,
                knowledgePoint = ui.knowledgePoints.firstOrNull() ?: "",
                questionType = ui.questionType,
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
 * 解题步骤卡：带序号圆标的分步列表，正文走 MathText（`e^x`、`\frac{}{}` 按公式渲染）。
 *
 * 步骤里常常夹着上标和分式，直接 Text 会糊成一串符号，这里复用解析卡同一套渲染。
 */
@Composable
private fun StepsCard(steps: List<String>) {
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
                    Icons.Rounded.FormatListNumbered,
                    contentDescription = null,
                    tint = TesEduColors.Brand,
                    modifier = Modifier.size(15.dp)
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                "解题步骤",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary,
                modifier = Modifier.weight(1f)
            )
            InfoPill("${steps.size} 步", TesEduColors.TextSecondary, TesEduColors.FieldBg)
        }
        Spacer(Modifier.height(12.dp))
        steps.forEachIndexed { index, step ->
            Row(Modifier.fillMaxWidth()) {
                Box(
                    Modifier
                        .size(22.dp)
                        .clip(CircleShape)
                        .background(TesEduColors.BrandSoft),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        "${index + 1}",
                        style = MaterialTheme.typography.labelSmall,
                        color = TesEduColors.Brand,
                        fontWeight = FontWeight.Bold
                    )
                }
                Spacer(Modifier.width(10.dp))
                MathText(
                    step,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextPrimary,
                    modifier = Modifier
                        .weight(1f)
                        .padding(top = 1.dp)
                )
            }
            if (index < steps.lastIndex) {
                Spacer(Modifier.height(10.dp))
            }
        }
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
    questionType: String? = null,
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
                        questionType = questionType,
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

/**
 * 数字人视频全屏播放器：讲解页与错题本共用。
 *
 * 播的是已经下载到 `cacheDir` 的本地文件（绝对路径），不依赖任何网络鉴权头；
 * VideoView 是框架控件，免新增 gradle 依赖。播完 / 出错 / 点关闭都收回弹窗。
 */
@Composable
private fun VideoPlayerDialog(path: String, onDismiss: () -> Unit) {
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Box(
            Modifier
                .fillMaxSize()
                .background(Color.Black)
        ) {
            AndroidView(
                factory = { ctx ->
                    VideoView(ctx).apply {
                        setVideoPath(path)
                        val mc = MediaController(ctx)
                        mc.setAnchorView(this)
                        setMediaController(mc)
                        setOnPreparedListener { start() }
                        setOnCompletionListener { onDismiss() }
                        setOnErrorListener { _, _, _ ->
                            onDismiss()
                            true
                        }
                    }
                },
                modifier = Modifier.fillMaxSize()
            )
            Box(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(16.dp)
                    .clickable { onDismiss() }
            ) {
                Icon(
                    Icons.Rounded.Close,
                    contentDescription = "关闭",
                    tint = Color.White,
                    modifier = Modifier.size(28.dp)
                )
            }
        }
    }
}

/**
 * 讲解页（#28 数字人讲题短视频的短期落地）：
 * 在后端视频管线（TTS + 数字人驱动 + ffmpeg）就绪前，先把已成熟的「讲解文案」
 * （SolveViewModel 的 answer / steps / knowledgePoints）做成图文讲解页，
 * 公式走 MathText 渲染，避免「讲解」tab 一直是空壳。
 *
 * 题目来源两条路：
 * ① 错题本点「看解析」→ AppBridge.postQuestion 投题 → 这里 LaunchedEffect 消费并拉讲解；
 * ② 学生在本页直接粘贴题干 →「获取讲解」按钮调用 solve（不带作答，纯讲解）。
 */
@Composable
fun ExplainScreen(
    vm: SolveViewModel = viewModel()
) {
    val ui by vm.ui.collectAsState()
    val pendingQuestion by AppBridge.pendingQuestion.collectAsState()
    var question by remember { mutableStateOf("") }

    // 「听讲解」语音试听：设备内置离线 TTS，离开页面时释放，避免引擎泄漏
    val ttsContext = LocalContext.current
    val ttsReady = remember { mutableStateOf(false) }
    val ttsSpeaking = remember { mutableStateOf(false) }
    val tts = remember {
        TtsController(ttsContext).also { c ->
            c.onState = { r, s -> ttsReady.value = r; ttsSpeaking.value = s }
        }
    }
    DisposableEffect(Unit) { onDispose { tts.shutdown() } }

    // 「看视频讲解」：带 Bearer 下载到私有缓存后，用本地绝对路径全屏播放。
    // 不能用 video_url 直链（相对路径 + VideoView 发不出 Authorization 头）。
    var playerPath by remember { mutableStateOf<String?>(null) }
    var videoLoading by remember { mutableStateOf(false) }
    // 视频目录：by-question 是逐字精确匹配，教师端批量生成的视频学生本来完全
    // 看不到。未命中（或题干为空）时列出本租户已出片视频，点任意一条即可播放。
    var videoCatalog by remember { mutableStateOf<List<ExplanationVideoItem>>(emptyList()) }
    var showVideoCatalog by remember { mutableStateOf(false) }
    var catalogHint by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

    suspend fun openVideoCatalog(hint: String) {
        videoLoading = true
        try {
            val items = listExplanationVideos()
            if (items.isEmpty()) {
                Toast.makeText(ttsContext, "暂无已生成的讲解视频", Toast.LENGTH_SHORT).show()
            } else {
                videoCatalog = items
                catalogHint = hint
                showVideoCatalog = true
            }
        } catch (e: Exception) {
            Toast.makeText(
                ttsContext, "加载视频列表失败：${e.message ?: "请稍后重试"}", Toast.LENGTH_SHORT
            ).show()
        } finally {
            videoLoading = false
        }
    }

    // 错题本/他处投来的题目：消费后即拉讲解，并预填输入框
    LaunchedEffect(pendingQuestion) {
        val p = pendingQuestion
        if (p != null && p.text.isNotBlank()) {
            question = p.text
            AppBridge.consumeQuestion()
            vm.solve(p.text)
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
            title = "讲解",
            subtitle = "分步解析与公式推导，帮你把题真正想通",
            badge = "AI 解析"
        )
        Spacer(Modifier.height(18.dp))

        SoftFieldCard(
            label = "题目",
            hint = "把题目放进来，点「获取讲解」看分步解析",
            value = question,
            onValueChange = { question = it },
            placeholder = "例如：已知 sinA=3/5，A 为锐角，求 cos2A…",
            minLines = 3,
            leadingIcon = Icons.Rounded.Quiz,
            mathPreview = true
        )
        Spacer(Modifier.height(12.dp))
        GradientButton(
            text = if (ui.loading) "生成讲解中…" else "获取讲解",
            icon = Icons.Rounded.AutoAwesome,
            enabled = question.isNotBlank() && !ui.loading
        ) {
            // 记录来源题干：触类页据此自动生成变式题
            AppBridge.setLastQuestion(question)
            vm.solve(question)
        }

        // 「听讲解」：把参考答案 + 分步 + 考点拼成可读文本朗读（设备离线 TTS）
        Spacer(Modifier.height(12.dp))
        val hasExplain = ui.answer.isNotBlank() || ui.visibleSteps.isNotEmpty() ||
            ui.knowledgePoints.isNotEmpty()
        SoftOutlinedButton(
            text = if (ttsSpeaking.value) "停止朗读" else "听讲解",
            icon = if (ttsSpeaking.value) Icons.Rounded.Stop else Icons.Rounded.VolumeUp,
            enabled = ttsReady.value && hasExplain
        ) {
            if (ttsSpeaking.value) {
                tts.stop()
            } else {
                val sb = StringBuilder()
                if (ui.answer.isNotBlank()) {
                    sb.append("参考答案：").append(toSpeechText(ui.answer)).append("。")
                }
                ui.visibleSteps.forEachIndexed { i, s ->
                    sb.append("第${i + 1}步：").append(toSpeechText(s)).append("。")
                }
                if (ui.knowledgePoints.isNotEmpty()) {
                    sb.append("涉及考点：").append(ui.knowledgePoints.joinToString("、")).append("。")
                }
                tts.speak(sb.toString())
            }
        }
        if (!ttsReady.value && hasExplain) {
            Spacer(Modifier.height(8.dp))
            Text(
                "设备未安装中文语音包，暂不支持朗读",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        // 「看视频讲解」：查任务 → 按 id 带鉴权取流下载到本地 → 本地绝对路径播放。
        // 不再要求先「获取讲解」：看视频与文字解析互不依赖，之前按钮在空结果时
        // 一直是灰的，学生直接点会以为功能不能用。
        SoftOutlinedButton(
            text = if (videoLoading) "视频加载中…" else "看视频讲解",
            icon = Icons.Rounded.PlayArrow,
            enabled = !videoLoading
        ) {
            scope.launch {
                if (question.isBlank()) {
                    openVideoCatalog("已有讲解视频，点任意一条即可播放")
                    return@launch
                }
                videoLoading = true
                try {
                    val resp = LoginManager.withAuth {
                        ApiClient.instance.explanationVideo(question)
                    }.getOrThrow()
                    if (resp.status == "done" && !resp.videoUrl.isNullOrBlank() &&
                        !resp.id.isNullOrBlank()
                    ) {
                        val file = downloadExplanationVideo(ttsContext, resp.id!!)
                        playerPath = file.absolutePath
                    } else {
                        // 逐字没匹配上不直接判「没有视频」，把目录拿出来让学生选
                        openVideoCatalog("本题暂无对应视频，以下是已有的讲解视频")
                    }
                } catch (e: Exception) {
                    Toast.makeText(
                        ttsContext,
                        "加载视频讲解失败：${e.message ?: "请稍后重试"}",
                        Toast.LENGTH_SHORT
                    ).show()
                } finally {
                    videoLoading = false
                }
            }
        }
        Text(
            text = "查看全部讲解视频",
            style = MaterialTheme.typography.labelMedium,
            color = TesEduColors.Brand,
            modifier = Modifier
                .padding(top = 8.dp)
                .clickable(enabled = !videoLoading) {
                    scope.launch { openVideoCatalog("已有讲解视频，点任意一条即可播放") }
                }
        )

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
                    "正在生成讲解…",
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

        if (ui.answer.isNotBlank()) {
            Spacer(Modifier.height(16.dp))
            AnswerCard(ui.answer)
        }

        if (ui.visibleSteps.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
            StepsCard(ui.visibleSteps)
        }

        if (ui.knowledgePoints.isNotEmpty()) {
            Spacer(Modifier.height(16.dp))
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
                        "涉及考点",
                        style = MaterialTheme.typography.titleSmall,
                        color = TesEduColors.TextPrimary
                    )
                }
                Spacer(Modifier.height(12.dp))
                Row(
                    Modifier
                        .fillMaxWidth()
                        .horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    ui.knowledgePoints.forEach { kp ->
                        InfoPill(kp, TesEduColors.TextSecondary, TesEduColors.FieldBg)
                    }
                }
            }
        }

        Spacer(Modifier.height(28.dp))
    }

    // 数字人视频全屏播放器：讲解页与错题本共用同一个 composable（本地文件播放）。
    playerPath?.let { path ->
        VideoPlayerDialog(path = path, onDismiss = { playerPath = null })
    }

    // 视频目录：题干逐字没匹配上时，让学生能从「已出片视频」里直接选一条播放，
    // 而不是弹一句「暂未生成」就结束（教师端明明已经生成过视频）。
    if (showVideoCatalog) {
        Dialog(onDismissRequest = { showVideoCatalog = false }) {
            SoftCard {
                Text(
                    "讲解视频",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    catalogHint,
                    style = MaterialTheme.typography.bodySmall,
                    color = TesEduColors.TextSecondary
                )
                Spacer(Modifier.height(12.dp))
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    videoCatalog.forEach { item ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(TesEduColors.FieldBg)
                                .clickable(enabled = !videoLoading) {
                                    scope.launch {
                                        showVideoCatalog = false
                                        videoLoading = true
                                        try {
                                            playerPath = downloadExplanationVideo(
                                                ttsContext, item.id
                                            ).absolutePath
                                        } catch (e: Exception) {
                                            Toast.makeText(
                                                ttsContext,
                                                "加载视频失败：${e.message ?: "请稍后重试"}",
                                                Toast.LENGTH_SHORT
                                            ).show()
                                        } finally {
                                            videoLoading = false
                                        }
                                    }
                                }
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                Icons.Rounded.PlayArrow,
                                contentDescription = null,
                                tint = TesEduColors.Brand
                            )
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    item.questionText ?: "讲解视频",
                                    maxLines = 2,
                                    style = MaterialTheme.typography.bodyMedium
                                )
                                val dur = item.durationSec
                                if (dur != null) {
                                    Text(
                                        "时长 %.1fs".format(dur),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = TesEduColors.TextMuted
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                    }
                }
                TextButton(onClick = { showVideoCatalog = false }) { Text("关闭") }
            }
        }
    }
}

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
 * 复习页（T-R + 间隔复习）：错题本 / 背诵卡两个分段。
 *
 * 归档入口在输入页的判分卡上（做错 / 还没写过程才收，判对的不收）——
 * 不搞自动全量归档：那样随手一试的题也会进来，错题本很快失去意义。
 *
 * 两段都是「本地优先 + 联网同步」：进页面触发一次同步，之后的新增/删除/
 * 评分都先落本地（离线可用），联网时再与后端对账。
 */
@Composable
fun ReviewScreen(
    onNavigate: (String) -> Unit = {},
    vm: WrongBookViewModel = viewModel(),
    drillVm: DrillViewModel = viewModel()
) {
    val ctx = LocalContext.current
    var tab by remember { mutableStateOf(ReviewTab.WRONG) }
    val items by vm.items.collectAsState()
    val focusKp by AppBridge.pendingFocusKp.collectAsState()
    val focusTitle by AppBridge.pendingFocusTitle.collectAsState()
    // 删除是破坏性操作，先问一句再删
    var pendingRemove by remember { mutableStateOf<WrongQuestion?>(null) }
    // M3-9：重做弹窗（判分在服务端，结果只展示不本地推断）
    var redoTarget by remember { mutableStateOf<WrongQuestion?>(null) }

    // 进入页面同步一次：不做后台定时轮询（学生端没必要，也费电）
    LaunchedEffect(Unit) {
        vm.sync()
        drillVm.load()
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))
        TesHero(
            title = "错题 · 背诵",
            subtitle = "错了的题常回头，该背的卡按时来",
            badge = "本地离线 · ${items.size} 道"
        )

        Spacer(Modifier.height(14.dp))
        ReviewTabSwitch(selected = tab, onSelect = { tab = it })

        Spacer(Modifier.height(14.dp))
        when (tab) {
            ReviewTab.WRONG -> WrongSection(
                vm = vm,
                onNavigate = onNavigate,
                onMakeCard = { q ->
                    drillVm.addFromWrong(q) { added ->
                        Toast.makeText(
                            ctx,
                            if (added) "已做成背诵卡，去「背诵」页就能背到" else "这道题没有题干，做不成卡",
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                },
                onRemove = { pendingRemove = it },
                onRedo = { redoTarget = it }
            )

            ReviewTab.DRILL -> DrillSection(
                vm = drillVm,
                focusKp = focusKp,
                focusTitle = focusTitle
            )
        }

        Spacer(Modifier.height(28.dp))
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
                    "「${q.question.take(40)}…」将从错题本里移除，联网后服务端记录也会一起删掉。",
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

    redoTarget?.let { q ->
        RedoDialog(
            q = q,
            vm = vm,
            onDismiss = {
                vm.clearRedo()
                redoTarget = null
            }
        )
    }
}

/** 两个分段：错题 / 背诵。 */
private enum class ReviewTab(val label: String) { WRONG("错题"), DRILL("背诵") }

/** 分段控件：选中项用品牌色浅底 + 品牌色文字，未选中只有浅灰底。 */
@Composable
private fun ReviewTabSwitch(selected: ReviewTab, onSelect: (ReviewTab) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(TesEduColors.FieldBg)
            .padding(4.dp)
    ) {
        ReviewTab.entries.forEach { tab ->
            val isSelected = tab == selected
            Box(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(13.dp))
                    .background(if (isSelected) TesEduColors.Surface else Color.Transparent)
                    .clickable { onSelect(tab) }
                    .padding(vertical = 10.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    tab.label,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
                    color = if (isSelected) TesEduColors.Brand else TesEduColors.TextMuted
                )
            }
        }
    }
}

// ---------------------------------------------------------------- 错题段

@Composable
private fun WrongSection(
    vm: WrongBookViewModel,
    onNavigate: (String) -> Unit,
    onMakeCard: (WrongQuestion) -> Unit,
    onRemove: (WrongQuestion) -> Unit,
    onRedo: (WrongQuestion) -> Unit
) {
    val all by vm.items.collectAsState()
    val visible by vm.visible.collectAsState()
    val kpOptions by vm.kpOptions.collectAsState()
    val paperOptions by vm.paperOptions.collectAsState()
    val qtypeOptions by vm.questionTypeOptions.collectAsState()
    val filter by vm.filter.collectAsState()
    val stats by vm.stats.collectAsState()
    val pending by vm.hasPending.collectAsState()
    val syncing by vm.syncing.collectAsState()
    val error by vm.error.collectAsState()

    // 每道错题一条专属讲解视频：按钮在卡片内，讲稿按「题干+参考答案+我的作答」现拼，
    // ensure 幂等排队 → 轮询出片 → 下载到 cacheDir → 本地全屏播放。
    val ctx = LocalContext.current
    val videoScope = rememberCoroutineScope()
    var videoBusyId by remember { mutableStateOf<Long?>(null) }
    var videoHint by remember { mutableStateOf<String?>(null) }
    var videoPendingIds by remember { mutableStateOf(setOf<Long>()) }
    var playerPath by remember { mutableStateOf<String?>(null) }

    fun playWrongVideo(q: WrongQuestion) {
        if (videoBusyId != null) return
        if (q.question.isBlank()) {
            Toast.makeText(ctx, "这道题没有题干，没法生成讲解视频", Toast.LENGTH_SHORT).show()
            return
        }
        videoScope.launch {
            videoBusyId = q.id
            videoHint = "正在为这道题准备讲解视频…"
            try {
                val id = ensurePlayableVideoId(
                    question = q.question,
                    script = wrongVideoScript(q),
                    knowledgePoint = q.knowledgePoint.takeIf { it.isNotBlank() }
                ) { hint -> videoHint = hint }
                if (id == null) {
                    // SaaS 仍在渲染：不弹错误、不卡按钮，卡片保留「生成中」状态
                    videoPendingIds = videoPendingIds + q.id
                    Toast.makeText(
                        ctx, "讲解视频已开始生成，几分钟后再点这里就能看", Toast.LENGTH_SHORT
                    ).show()
                } else {
                    videoPendingIds = videoPendingIds - q.id
                    val file = downloadExplanationVideo(ctx, id)
                    playerPath = file.absolutePath
                }
            } catch (e: Exception) {
                Toast.makeText(
                    ctx, "视频讲解失败：${e.message ?: "请稍后重试"}", Toast.LENGTH_SHORT
                ).show()
            } finally {
                videoBusyId = null
                videoHint = null
            }
        }
    }

    // 知识点编码->中文名：联网同步后会由 WrongBookViewModel 灌入；离线时为空，回退到编码。
    val kpNames by KpNameCache.names.collectAsState()

    // 断网只是轻提示：本地数据照常可读，不打断复习
    error?.let { msg ->
        SoftNotice(
            text = msg,
            icon = Icons.Rounded.CloudOff,
            color = TesEduColors.Amber,
            bg = TesEduColors.AmberSoft
        )
        Spacer(Modifier.height(12.dp))
    }

    if (syncing || pending > 0) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            if (syncing) {
                CircularProgressIndicator(
                    Modifier.size(14.dp),
                    strokeWidth = 2.dp,
                    color = TesEduColors.Brand
                )
            }
            if (pending > 0) {
                InfoPill("未同步 $pending 条", TesEduColors.Amber, TesEduColors.AmberSoft)
            }
        }
        Spacer(Modifier.height(12.dp))
    }

    if (all.isEmpty()) {
        Spacer(Modifier.height(24.dp))
        EmptyState(
            icon = Icons.Rounded.Bookmark,
            title = "错题本还是空的",
            subtitle = "做题被判错时点「收进错题本」，就会存到这里"
        )
        return
    }

    WrongStatsCard(total = all.size, items = all, stats = stats, kpNames = kpNames)

    Spacer(Modifier.height(14.dp))
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "筛选",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "按来源卷 / 知识点 / 错因 / 题型挑着看",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
            Spacer(Modifier.weight(1f))
            if (filter.active) {
                Text(
                    "全部",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.Brand,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.clickable { vm.setFilter(WrongFilter()) }
                )
            }
        }
        if (paperOptions.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            // M3-9 按卷筛选：只列本地确实有卷归属的错题，手工收的题不占位
            ChipRow(
                title = "来源卷",
                options = paperOptions.map { it.toString() },
                selected = filter.paperId?.toString(),
                labelOf = { "第 $it 卷" },
                onSelect = { vm.setFilter(filter.copy(paperId = it?.toLongOrNull())) }
            )
        }
        if (kpOptions.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            ChipRow(
                title = "知识点",
                options = kpOptions,
                selected = filter.kp,
                labelOf = { kpNames[it] ?: it },
                onSelect = { vm.setFilter(filter.copy(kp = it)) }
            )
        }
        val errorOptions = (all.mapNotNull { it.errorType } +
            (stats?.byErrorType?.keys ?: emptyList())).distinct().sorted()
        if (errorOptions.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            ChipRow(
                title = "错因",
                options = errorOptions,
                selected = filter.errorType,
                labelOf = { ERROR_TYPE_LABEL[it] ?: it },
                onSelect = { vm.setFilter(filter.copy(errorType = it)) }
            )
        }
        if (qtypeOptions.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            // 题型筛选：choice/blank/subjective -> 选择题/填空题/解答题
            ChipRow(
                title = "题型",
                options = qtypeOptions,
                selected = filter.questionType,
                labelOf = { QTYPE_LABEL[it] ?: it },
                onSelect = { vm.setFilter(filter.copy(questionType = it)) }
            )
        }
    }

    Spacer(Modifier.height(12.dp))
    if (visible.isEmpty()) {
        SoftNotice(
            text = "当前筛选下没有错题，点右上角「全部」看回全部。",
            icon = Icons.Rounded.Info,
            color = TesEduColors.Amber,
            bg = TesEduColors.AmberSoft
        )
    } else {
        Text(
            "最近错的排在最前面。点「重练这道」会把题干带回输入页。",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
        visible.forEach { q ->
            Spacer(Modifier.height(14.dp))
            WrongCard(
                q = q,
                mastery = stats?.mastery?.firstOrNull { it.kp == q.knowledgePoint },
                kpName = kpNames[q.knowledgePoint] ?: q.knowledgePoint,
                onPractice = {
                    // 选择题把选项一并带过去，输入页可展示 A/B/C/D（只读）。
                    AppBridge.postQuestion(q.question, q.options, q.questionType)
                    onNavigate(Screen.Input.route)
                },
                onMakeCard = { onMakeCard(q) },
                onRemove = { onRemove(q) },
                onRedo = { onRedo(q) },
                onExplain = {
                    // 把题干（选择题含选项 JSON + 题型）投到讲解页，再跳转过去
                    AppBridge.postQuestion(q.question, q.options, q.questionType)
                    onNavigate(Screen.Explain.route)
                },
                onVideo = { playWrongVideo(q) },
                videoBusy = videoBusyId == q.id,
                videoPending = q.id in videoPendingIds,
                videoHint = if (videoBusyId == q.id) videoHint else null
            )
        }
    }

    playerPath?.let { path ->
        VideoPlayerDialog(path = path, onDismiss = { playerPath = null })
    }
}

/** 统计条：总数 + 错因 Top 3 + 掌握度（后两者拿不到就隐藏对应部分）。 */
@Composable
private fun WrongStatsCard(
    total: Int,
    items: List<WrongQuestion>,
    stats: WrongStats?,
    kpNames: Map<String, String> = emptyMap()
) {
    // 服务端给了分布就用服务端的，没给就按本地算（离线也能看出自己错在哪）
    val byType = stats?.byErrorType?.takeIf { it.isNotEmpty() }
        ?: items.mapNotNull { it.errorType }.groupingBy { it }.eachCount()
    val top = byType.entries.sortedByDescending { it.value }.take(3)

    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Rounded.TipsAndUpdates,
                contentDescription = null,
                tint = TesEduColors.Brand,
                modifier = Modifier.size(18.dp)
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "共 $total 道错题",
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
        }
        if (top.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            Text(
                top.joinToString(" · ") { (code, n) -> "${ERROR_TYPE_LABEL[code] ?: code} $n" },
                style = MaterialTheme.typography.bodySmall,
                color = TesEduColors.TextSecondary
            )
        }
        // 掌握度来自后端统计：拿不到就整块隐藏，不报错也不编数字
        val mastery = stats?.mastery?.takeIf { it.isNotEmpty() }
        if (mastery != null) {
            Spacer(Modifier.height(10.dp))
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                mastery.take(3).forEach { m ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            kpNames[m.kp] ?: m.kp,
                            style = MaterialTheme.typography.labelSmall,
                            color = TesEduColors.TextMuted,
                            modifier = Modifier.weight(1f)
                        )
                        MasteryPill(m)
                    }
                }
            }
        }
    }
}

/** 「该知识点掌握度 62%（较上周 +10%）」。 */
@Composable
private fun MasteryPill(m: MasteryPoint) {
    val up = m.delta >= 0
    val percent = (m.mastery * 100).toInt().coerceIn(0, 100)
    val delta = (m.delta * 100).toInt()
    val text = "掌握度 $percent%（较上周 ${if (up) "+" else ""}$delta%）"
    InfoPill(
        text,
        if (up) TesEduColors.Success else TesEduColors.Danger,
        if (up) TesEduColors.SuccessSoft else TesEduColors.DangerSoft
    )
}

/** 横向滚动的筛选 chips，「全部」= 取消该维度筛选。 */
@Composable
private fun ChipRow(
    title: String,
    options: List<String>,
    selected: String?,
    labelOf: (String) -> String,
    onSelect: (String?) -> Unit
) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            title,
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
        Spacer(Modifier.height(6.dp))
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            FilterChip("全部", selected == null) { onSelect(null) }
            options.forEach { code ->
                FilterChip(labelOf(code), selected == code) { onSelect(code) }
            }
        }
    }
}

@Composable
private fun FilterChip(text: String, selected: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(50)
    val base = Modifier
        .clip(shape)
        .background(if (selected) TesEduColors.BrandSoft else TesEduColors.Bg)
    val framed = if (selected) base.border(1.dp, TesEduColors.Brand, shape) else base
    Box(
        framed
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 7.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
            color = if (selected) TesEduColors.BrandDeep else TesEduColors.TextSecondary
        )
    }
}

/**
 * 把一条错题拼成数字人的讲稿：题干摘录 + 参考答案 + 学生当时答错的内容。
 *
 * 讲稿越长，IVH 的 TTS 音频越长、SaaS 渲染越慢（实测近百字要七八分钟以上），
 * 所以这里刻意压到 60 字以内做「一句话点拨」，完整的分步解析仍走「看解析」。
 */
private fun wrongVideoScript(q: WrongQuestion): String {
    val answer = q.correction?.takeIf { it.isNotBlank() } ?: q.correctAnswer
    val sb = StringBuilder("这道题：")
    sb.append(q.question.take(28)).append("。")
    if (answer.isNotBlank()) sb.append("答案").append(answer.take(18)).append("。")
    if (q.myAnswer.isNotBlank()) sb.append("你答的是").append(q.myAnswer.take(10)).append("，注意。")
    return sb.toString().take(60)
}

/**
 * 单条错题：错因 + 题干 + 我的作答 + 纠正，以及重练 / 背诵卡 / 移出等出口。
 * [onVideo] 是这道题专属的数字人讲解视频（不是全局视频列表）。
 */
@Composable
private fun WrongCard(
    q: WrongQuestion,
    mastery: MasteryPoint?,
    kpName: String = q.knowledgePoint,
    onPractice: () -> Unit,
    onMakeCard: () -> Unit,
    onRemove: () -> Unit,
    onRedo: () -> Unit,
    onExplain: () -> Unit,
    onVideo: () -> Unit,
    videoBusy: Boolean = false,
    videoPending: Boolean = false,
    videoHint: String? = null
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
                if (kpName.isNotBlank()) {
                    Text(
                        kpName,
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

        // 选择题展示选项（只读，无对错高亮）；选项存为 ExamOption 的 JSON
        if (q.questionType == "choice") {
            val opts = remember(q.options) { parseWrongOptions(q.options) }
            if (opts.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    opts.forEach { opt ->
                        WrongOptionRow(opt.label, opt.content)
                    }
                }
            }
        }

        if (!q.errorType.isNullOrBlank() || !q.questionType.isNullOrBlank()) {
            Spacer(Modifier.height(10.dp))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                q.questionType?.let {
                    InfoPill(
                        QTYPE_LABEL[it] ?: it,
                        TesEduColors.TextSecondary,
                        TesEduColors.FieldBg
                    )
                }
                q.errorType?.let {
                    InfoPill(
                        "错因 · ${ERROR_TYPE_LABEL[it] ?: it}",
                        TesEduColors.Danger,
                        TesEduColors.DangerSoft
                    )
                }
            }
            q.firstWrongStep?.let {
                Spacer(Modifier.height(6.dp))
                Text(
                    "首个出错步骤：第 $it 步",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }

        if (mastery != null) {
            Spacer(Modifier.height(10.dp))
            MasteryPill(mastery)
        }

        if (q.myAnswer.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            SoftQuote("我的作答", q.myAnswer, TesEduColors.Amber, TesEduColors.AmberSoft)
        }
        if (!q.correction.isNullOrBlank()) {
            Spacer(Modifier.height(8.dp))
            SoftQuote("纠正", q.correction.orEmpty(), TesEduColors.Success, TesEduColors.SuccessSoft)
        } else if (q.correctAnswer.isNotBlank()) {
            Spacer(Modifier.height(8.dp))
            SoftQuote("参考答案", q.correctAnswer, TesEduColors.Success, TesEduColors.SuccessSoft)
        }

        Spacer(Modifier.height(14.dp))
        // 这道题专属的数字人讲解视频：首次点击排队生成，出片后点这里直接播放
        SoftOutlinedButton(
            text = when {
                videoBusy -> "视频讲解准备中…"
                videoPending -> "讲解视频生成中 · 点这里查看"
                else -> "视频讲解"
            },
            icon = Icons.Rounded.PlayArrow,
            enabled = !videoBusy
        ) { onVideo() }
        val hint = videoHint
            ?: if (videoPending && !videoBusy) "数字人出片约需几分钟，稍后再点即可观看" else null
        if (hint != null) {
            Spacer(Modifier.height(6.dp))
            Text(
                hint,
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.Brand
            )
        }
        Spacer(Modifier.height(10.dp))
        // 看解析：进入讲解页查看分步讲解与公式推导（#28 短期落地）
        SoftOutlinedButton(
            text = "看解析",
            icon = Icons.Rounded.MenuBook
        ) { onExplain() }
        Spacer(Modifier.height(10.dp))
        SoftOutlinedButton(
            text = "重做并判分",
            icon = Icons.Rounded.CheckCircle
        ) { onRedo() }
        Spacer(Modifier.height(10.dp))
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
        Spacer(Modifier.height(10.dp))
        SoftOutlinedButton(
            text = "做成背诵卡",
            icon = Icons.Rounded.AutoStories
        ) { onMakeCard() }
    }
}

/** 把错题表存的选项 JSON（[`{"label":"A","content":"A. ..."}`]）解析成列表，失败返回空。 */
private fun parseWrongOptions(json: String?): List<ExamOption> {
    if (json.isNullOrBlank()) return emptyList()
    return runCatching {
        val type = object : TypeToken<List<ExamOption>>() {}.type
        Gson().fromJson<List<ExamOption>>(json, type) ?: emptyList()
    }.getOrDefault(emptyList())
}

/**
 * 错题本选项行。
 * - 默认只读（RedoDialog / 解题页展示用），无对错高亮。
 * - 传入 [onClick] 即变为可点选：选中态 [selected] 高亮描边，右侧显示勾选。
 */
@Composable
private fun WrongOptionRow(
    label: String,
    content: String,
    selected: Boolean = false,
    onClick: (() -> Unit)? = null
) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .then(
                if (onClick != null) Modifier.clickable(
                    enabled = true,
                    role = Role.RadioButton,
                    onClick = onClick
                ) else Modifier
            )
            .background(if (selected) TesEduColors.BrandSoft else TesEduColors.Surface)
            .border(
                1.dp,
                if (selected) TesEduColors.Brand else TesEduColors.Divider,
                shape
            )
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(26.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(if (selected) TesEduColors.Brand else TesEduColors.FieldBg),
            contentAlignment = Alignment.Center
        ) {
            Text(
                label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Bold,
                color = if (selected) TesEduColors.Surface else TesEduColors.TextSecondary
            )
        }
        Spacer(Modifier.width(10.dp))
        // 后端存的 content 常自带 "A. " 前缀（与 label 重复），展示时去掉，保持和选项一致的观感
        val display = content.removePrefix("$label. ").removePrefix("$label.")
        MathText(
            display,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
        if (selected) {
            Spacer(Modifier.width(8.dp))
            Icon(
                Icons.Rounded.CheckCircle,
                contentDescription = null,
                tint = TesEduColors.Brand,
                modifier = Modifier.size(20.dp)
            )
        }
    }
}

/**
 * M3-9 重做弹窗：选择题直接填选项，多空题用 `；` 或换行分隔。
 *
 * 判分权只在服务端：这里展示的每一句都来自 [WrongRedoResult]，
 * 客户端不根据 `correctAnswer` 自行比对（多选题序、部分分、答案值集合
 * 的规则都以服务端为准）。
 */
@Composable
private fun RedoDialog(
    q: WrongQuestion,
    vm: WrongBookViewModel,
    onDismiss: () -> Unit
) {
    var input by remember(q.id) { mutableStateOf("") }
    val result by vm.redoResult.collectAsState()
    val redoing by vm.redoing.collectAsState()
    val error by vm.redoError.collectAsState()
    val done = result != null

    AlertDialog(
        onDismissRequest = { if (!redoing) onDismiss() },
        shape = RoundedCornerShape(22.dp),
        containerColor = TesEduColors.Surface,
        title = {
            Text(
                "重做这道",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.TextPrimary
            )
        },
        text = {
            Column {
                MathText(
                    q.question,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextSecondary
                )
                // 选择题：重做时把选项一并列出，便于对照作答（只读，无对错高亮）。
                // 填空/解答题不处理，仅保留下方答案输入框。
                if (q.questionType == "choice") {
                    val redoOpts = remember(q.options) { parseWrongOptions(q.options) }
                    if (redoOpts.isNotEmpty()) {
                        Spacer(Modifier.height(12.dp))
                        Column(
                            Modifier.fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            redoOpts.forEach { opt -> WrongOptionRow(opt.label, opt.content) }
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                if (!done) {
                    SoftFieldCard(
                        label = "你的答案",
                        value = input,
                        onValueChange = { input = it },
                        hint = "多空用；分隔",
                        placeholder = "选择题填 A/AB；填空按空填写",
                        minLines = 2,
                        accent = TesEduColors.Brand,
                        leadingIcon = Icons.Rounded.EditNote
                    )
                }
                error?.let {
                    Spacer(Modifier.height(10.dp))
                    SoftNotice(
                        text = it,
                        icon = Icons.Rounded.CloudOff,
                        color = TesEduColors.Amber,
                        bg = TesEduColors.AmberSoft
                    )
                }
                result?.let { res ->
                    Spacer(Modifier.height(10.dp))
                    val good = res.resolved
                    SoftNotice(
                        text = redoFeedback(res),
                        icon = if (good) Icons.Rounded.CheckCircle else Icons.Rounded.Info,
                        color = if (good) TesEduColors.Success else TesEduColors.Amber,
                        bg = if (good) TesEduColors.SuccessSoft else TesEduColors.AmberSoft
                    )
                }
            }
        },
        confirmButton = {
            if (done) {
                TextButton(onClick = onDismiss) { Text("完成") }
            } else {
                TextButton(
                    enabled = input.isNotBlank() && !redoing,
                    onClick = {
                        val blanks = splitRedoBlanks(input)
                        vm.redo(q, input, blanks)
                    }
                ) {
                    Text(if (redoing) "判分中…" else "提交判分")
                }
            }
        },
        dismissButton = {
            when {
                // 已归档的题不能再提交一次（服务端 404）；只留「完成」
                result?.resolved == true -> Unit
                done -> TextButton(onClick = {
                    input = ""
                    vm.clearRedo()
                }) { Text("再试一次") }
                else -> TextButton(enabled = !redoing, onClick = onDismiss) { Text("取消") }
            }
        }
    )
}

// ---------------------------------------------------------------- 背诵段

@Composable
private fun DrillSection(vm: DrillViewModel, focusKp: String?, focusTitle: String?) {
    val ui by vm.ui.collectAsState()

    if (focusKp != null) {
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
                        "${focusTitle ?: focusKp} · $focusKp",
                        style = MaterialTheme.typography.titleSmall,
                        color = TesEduColors.TextPrimary
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                "到期卡片按 SM-2 排程，背完自评即可；知识点会体现在卡面上。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }
        Spacer(Modifier.height(14.dp))
    }

    ui.error?.let { msg ->
        SoftNotice(
            text = msg,
            icon = Icons.Rounded.CloudOff,
            color = TesEduColors.Amber,
            bg = TesEduColors.AmberSoft
        )
        Spacer(Modifier.height(12.dp))
    }

    ui.stats?.let { s ->
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            InfoPill("共 ${s.total} 张", TesEduColors.TextSecondary, TesEduColors.FieldBg)
            InfoPill("今日到期 ${s.due}", TesEduColors.BrandDeep, TesEduColors.BrandSoft)
            InfoPill("新增 ${s.newCount}", TesEduColors.Mint, TesEduColors.MintSoft)
            InfoPill(
                "留存 ${(s.retention * 100).toInt()}%",
                TesEduColors.Amber,
                TesEduColors.AmberSoft
            )
        }
        Spacer(Modifier.height(12.dp))
    }

    when {
        ui.finished -> DrillDoneCard(reviewed = ui.reviewed, nextDueAt = ui.nextDueAt) {
            vm.reset()
        }

        ui.started -> ui.current?.let { card ->
            DrillReviewCard(
                card = card,
                index = ui.index,
                total = ui.queue.size,
                onGrade = { vm.grade(it) }
            )
        }

        else -> DrillOverviewCard(dueCount = ui.dueCount, onStart = { vm.start() })
    }
}

/** 概览：显示到期数量 + 开始按钮；没有到期卡就给空态引导。 */
@Composable
private fun DrillOverviewCard(dueCount: Int, onStart: () -> Unit) {
    if (dueCount == 0) {
        Spacer(Modifier.height(16.dp))
        EmptyState(
            icon = Icons.Rounded.AutoStories,
            title = "今天没有到期的卡",
            subtitle = "去「错题」页把错题点「做成背诵卡」，新卡当天就会到期"
        )
        return
    }
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(TesEduColors.BrandSoft),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    Icons.Rounded.AutoStories,
                    contentDescription = null,
                    tint = TesEduColors.Brand,
                    modifier = Modifier.size(22.dp)
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    "今日到期 $dueCount 张",
                    style = MaterialTheme.typography.titleSmall,
                    color = TesEduColors.TextPrimary,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    "先看正面想一遍，点一下翻背面再自评",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }
        Spacer(Modifier.height(14.dp))
        GradientButton(
            text = "开始复习（$dueCount 张）",
            icon = Icons.Rounded.TipsAndUpdates
        ) { onStart() }
    }
}

/** 复习中：正面 → 点一下翻背面 → 四档自评。 */
@Composable
private fun DrillReviewCard(
    card: DrillCard,
    index: Int,
    total: Int,
    onGrade: (Int) -> Unit
) {
    // 换卡就回到正面：remember 的 key 是卡 id
    var flipped by remember(card.id) { mutableStateOf(false) }

    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            card.kp?.takeIf { it.isNotBlank() }?.let { kp ->
                InfoPill(kp, TesEduColors.BrandDeep, TesEduColors.BrandSoft)
                Spacer(Modifier.width(6.dp))
            }
            Spacer(Modifier.weight(1f))
            Text(
                "第 ${index + 1} / $total",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        Spacer(Modifier.height(12.dp))
        Text(
            if (flipped) "背面" else "正面",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
        Spacer(Modifier.height(6.dp))
        // 卡面常见 `x^2`、`\frac{1}{2}`，统一走公式渲染
        MathText(
            if (flipped) card.backText else card.frontText,
            style = MaterialTheme.typography.bodyLarge,
            color = TesEduColors.TextPrimary
        )

        Spacer(Modifier.height(16.dp))
        if (!flipped) {
            GradientButton(
                text = "翻到背面",
                icon = Icons.Rounded.Refresh
            ) { flipped = true }
            Spacer(Modifier.height(8.dp))
            Text(
                "先在心里答一遍，再翻面对答案——直接看背面等于没背。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        } else {
            Text(
                "刚才想得怎么样？",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
            Spacer(Modifier.height(10.dp))
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SoftOutlinedButton(
                        text = "忘了",
                        icon = Icons.Rounded.Cancel,
                        modifier = Modifier.weight(1f)
                    ) { onGrade(0) }
                    SoftOutlinedButton(
                        text = "模糊",
                        icon = Icons.Rounded.ErrorOutline,
                        modifier = Modifier.weight(1f)
                    ) { onGrade(3) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SoftOutlinedButton(
                        text = "会了",
                        icon = Icons.Rounded.CheckCircle,
                        modifier = Modifier.weight(1f)
                    ) { onGrade(4) }
                    SoftOutlinedButton(
                        text = "太简单",
                        icon = Icons.Rounded.AutoAwesome,
                        modifier = Modifier.weight(1f)
                    ) { onGrade(5) }
                }
            }
        }
    }
}

/** 完成态：今日复习 N 张 + 下次到期时间。 */
@Composable
private fun DrillDoneCard(reviewed: Int, nextDueAt: Long?, onBack: () -> Unit) {
    SoftCard {
        Box(
            Modifier
                .size(46.dp)
                .clip(CircleShape)
                .background(TesEduColors.SuccessSoft),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                Icons.Rounded.CheckCircle,
                contentDescription = null,
                tint = TesEduColors.Success,
                modifier = Modifier.size(26.dp)
            )
        }
        Spacer(Modifier.height(12.dp))
        Text(
            "今日复习 $reviewed 张",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(6.dp))
        Text(
            nextDueAt?.let { "下次到期：${formatDateTime(it)}" } ?: "暂时没有排到后面的卡了",
            style = MaterialTheme.typography.bodySmall,
            color = TesEduColors.TextSecondary
        )
        Spacer(Modifier.height(14.dp))
        SoftOutlinedButton(
            text = "回到背诵概览",
            icon = Icons.Rounded.AutoStories
        ) { onBack() }
    }
}

private fun formatDateTime(millis: Long): String =
    SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(millis))
