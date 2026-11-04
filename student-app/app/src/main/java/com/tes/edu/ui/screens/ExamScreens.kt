package com.tes.edu.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AccountTree
import androidx.compose.material.icons.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ArrowForward
import androidx.compose.material.icons.rounded.Assignment
import androidx.compose.material.icons.rounded.AutoStories
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.CloudOff
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material.icons.rounded.Send
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.tes.edu.data.remote.ExamQuestionView
import com.tes.edu.data.remote.ExamResultDto
import com.tes.edu.data.remote.GraphNode
import com.tes.edu.data.remote.QuestionGraphDto
import com.tes.edu.data.remote.StudentExamItem
import com.tes.edu.ui.components.EmptyState
import com.tes.edu.ui.components.GradientButton
import com.tes.edu.ui.components.InfoPill
import com.tes.edu.ui.components.SoftCard
import com.tes.edu.ui.components.SoftFieldCard
import com.tes.edu.ui.components.SoftNotice
import com.tes.edu.ui.components.SoftOutlinedButton
import com.tes.edu.ui.components.SoftQuote
import com.tes.edu.ui.components.TesHero
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.util.MathText
import com.tes.edu.viewmodel.ExamSolutionState
import com.tes.edu.viewmodel.ExamStage
import com.tes.edu.viewmodel.ExamUiState
import com.tes.edu.viewmodel.ExamViewModel
import com.tes.edu.viewmodel.QuestionGraphState
import kotlin.math.roundToInt

/**
 * 作业闭环（M1-6）：列表 → 答题 → 结果。
 *
 * 刻意不走三条导航路由：作业是一个「进去做完再出来」的流程，用页内状态机
 * 切换，底部导航始终停在「作业」上，也不会给学生堆一串返回栈。
 */
@Composable
fun ExamScreen(
    vm: ExamViewModel = viewModel()
) {
    val ui by vm.ui.collectAsState()

    // 进页拉一次列表。放在 LaunchedEffect(Unit) 里：切回该 tab 会重新组合，
    // 自然就刷新了「老师刚下发的新卷」和「已提交」状态。
    LaunchedEffect(Unit) { vm.refresh() }

    when (ui.stage) {
        ExamStage.LIST -> ExamListBody(ui = ui, onRefresh = vm::refresh, onOpen = vm::open)
        ExamStage.ANSWERING -> ExamAnswerBody(
            ui = ui,
            onBack = vm::backToList,
            onSelect = vm::selectOption,
            onText = vm::setAnswer,
            onPrev = vm::prev,
            onNext = vm::next,
            onSubmit = vm::submit
        )
        ExamStage.RESULT -> ExamResultBody(
            ui = ui,
            onBack = vm::backToList,
            onRefresh = { ui.assignmentId.takeIf { it != 0L }?.let { vm.openResult(it, ui.title) } },
            onSolution = vm::loadSolution,
            // M3-6：图谱下沉到逐题卡片，展开该题才拉这份子图（不再跳整卷图谱页）
            onGraph = vm::loadQuestionGraph
        )
    }
}

// ================================================================== 列表
@Composable
private fun ExamListBody(
    ui: ExamUiState,
    onRefresh: () -> Unit,
    onOpen: (StudentExamItem) -> Unit
) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
    ) {
        Spacer(Modifier.height(12.dp))
        TesHero(
            title = "作业",
            subtitle = "老师下发的卷子，点开就能答；断网也能做，联网再交",
            badge = "作业闭环"
        )
        Spacer(Modifier.height(14.dp))

        if (ui.loading && ui.exams.isEmpty()) {
            LoadingBlock("正在取作业…")
        }

        ui.error?.let { msg ->
            SoftNotice(
                text = msg,
                icon = if (ui.offline) Icons.Rounded.CloudOff else Icons.Rounded.ErrorOutline,
                color = if (ui.offline) TesEduColors.TextSecondary else TesEduColors.Danger,
                bg = if (ui.offline) TesEduColors.FieldBg else TesEduColors.DangerSoft
            )
            Spacer(Modifier.height(10.dp))
            SoftOutlinedButton(text = "重试", icon = Icons.Rounded.Refresh, onClick = onRefresh)
            Spacer(Modifier.height(14.dp))
        }

        if (ui.loaded && ui.exams.isEmpty() && ui.error == null) {
            Box(Modifier.fillMaxWidth().height(320.dp)) {
                EmptyState(
                    icon = Icons.Rounded.Assignment,
                    title = "还没有作业",
                    subtitle = "老师下发后会出现在这里，先去看看错题本吧"
                )
            }
        }

        ui.exams.forEach { item ->
            ExamCard(item = item, onClick = { onOpen(item) })
            Spacer(Modifier.height(12.dp))
        }
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun ExamCard(item: StudentExamItem, onClick: () -> Unit) {
    SoftCard(Modifier.clip(RoundedCornerShape(20.dp)).clickable(onClick = onClick)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                item.title.ifBlank { "未命名试卷" },
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.TextPrimary,
                modifier = Modifier.weight(1f)
            )
            if (item.submitted) {
                InfoPill("已提交", TesEduColors.Success, TesEduColors.SuccessSoft)
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            InfoPill(item.paperTypeLabel, TesEduColors.Brand, TesEduColors.BrandSoft)
            Spacer(Modifier.width(8.dp))
            InfoPill("${item.questionCount} 题", TesEduColors.TextSecondary, TesEduColors.FieldBg)
            item.dueAt?.let {
                Spacer(Modifier.width(8.dp))
                InfoPill("截止 ${shortDate(it)}", TesEduColors.Amber, TesEduColors.AmberSoft)
            }
        }
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (item.submitted) "查看结果" else "开始作答",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = TesEduColors.Brand
            )
            Spacer(Modifier.width(4.dp))
            Icon(
                Icons.Rounded.ArrowForward,
                contentDescription = null,
                tint = TesEduColors.Brand,
                modifier = Modifier.size(16.dp)
            )
        }
    }
}

// ================================================================== 答题
@Composable
private fun ExamAnswerBody(
    ui: ExamUiState,
    onBack: () -> Unit,
    onSelect: (Int, String) -> Unit,
    onText: (Int, String) -> Unit,
    onPrev: () -> Unit,
    onNext: () -> Unit,
    onSubmit: () -> Unit
) {
    var confirmOpen by remember { mutableStateOf(false) }
    val q = ui.current

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
    ) {
        Spacer(Modifier.height(8.dp))
        // 顶部：返回 + 标题 + 进度
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) {
                Icon(Icons.Rounded.ArrowBack, contentDescription = "返回", tint = TesEduColors.Brand)
                Spacer(Modifier.width(4.dp))
                Text("作业", color = TesEduColors.Brand)
            }
            Spacer(Modifier.weight(1f))
            Text(
                ui.progressText,
                style = MaterialTheme.typography.labelLarge,
                color = TesEduColors.TextSecondary
            )
        }
        Text(
            ui.title.ifBlank { "作业" },
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(10.dp))
        LinearProgressIndicator(
            progress = {
                if (ui.total == 0) 0f else ui.answeredCount.toFloat() / ui.total.toFloat()
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(7.dp)
                .clip(RoundedCornerShape(50)),
            color = TesEduColors.Brand,
            trackColor = TesEduColors.Divider
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "已答 ${ui.answeredCount} / ${ui.total}",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )

        if (ui.draftRestored) {
            Spacer(Modifier.height(10.dp))
            InfoPill("已恢复上次作答", TesEduColors.Mint, TesEduColors.MintSoft)
        }
        ui.error?.let {
            Spacer(Modifier.height(10.dp))
            SoftNotice(
                text = it,
                icon = if (ui.offline) Icons.Rounded.CloudOff else Icons.Rounded.ErrorOutline,
                color = if (ui.offline) TesEduColors.TextSecondary else TesEduColors.Danger,
                bg = if (ui.offline) TesEduColors.FieldBg else TesEduColors.DangerSoft
            )
        }
        Spacer(Modifier.height(14.dp))

        if (ui.loading && q == null) {
            LoadingBlock("正在取卷…")
        } else if (q == null) {
            Box(Modifier.fillMaxWidth().height(280.dp)) {
                EmptyState(
                    icon = Icons.Rounded.Assignment,
                    title = "这份卷取不到",
                    subtitle = "可能是还没下发，或网络不通"
                )
            }
        } else {
            QuestionBlock(q = q, answer = ui.currentAnswer, onSelect = onSelect, onText = onText)
            Spacer(Modifier.height(16.dp))

            // 翻页
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Box(Modifier.weight(1f)) {
                    SoftOutlinedButton(
                        text = "上一题",
                        icon = Icons.Rounded.ArrowBack,
                        enabled = ui.index > 0,
                        onClick = onPrev
                    )
                }
                Box(Modifier.weight(1f)) {
                    SoftOutlinedButton(
                        text = "下一题",
                        icon = Icons.Rounded.ArrowForward,
                        enabled = ui.index < ui.total - 1,
                        onClick = onNext
                    )
                }
            }
            Spacer(Modifier.height(12.dp))
            GradientButton(
                text = if (ui.submitting) "判分中…" else "交卷判分",
                icon = Icons.Rounded.Send,
                enabled = ui.canSubmit,
                onClick = { if (ui.unansweredCount > 0) confirmOpen = true else onSubmit() }
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "交卷后立即判分：选择题当场出结果，填空/解答转老师批改",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }
        Spacer(Modifier.height(28.dp))
    }

    if (confirmOpen) {
        AlertDialog(
            onDismissRequest = { confirmOpen = false },
            title = { Text("还有 ${ui.unansweredCount} 题没答") },
            text = { Text("空题按错处理，确定现在交卷吗？作答已存在本机，交卷前也能返回继续答。") },
            confirmButton = {
                TextButton(onClick = { confirmOpen = false; onSubmit() }) { Text("交卷") }
            },
            dismissButton = {
                TextButton(onClick = { confirmOpen = false }) { Text("继续答") }
            }
        )
    }
}

@Composable
private fun QuestionBlock(
    q: ExamQuestionView,
    answer: String,
    onSelect: (Int, String) -> Unit,
    onText: (Int, String) -> Unit
) {
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            InfoPill("第 ${q.seq} 题", TesEduColors.Brand, TesEduColors.BrandSoft)
            Spacer(Modifier.width(8.dp))
            InfoPill(q.qtypeLabel, TesEduColors.TextSecondary, TesEduColors.FieldBg)
            if (q.multi) {
                Spacer(Modifier.width(8.dp))
                Text(
                    "可多选",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        // 题干常带公式，按公式渲染
        MathText(
            q.stem,
            style = MaterialTheme.typography.bodyLarge,
            color = TesEduColors.TextPrimary
        )
    }
    Spacer(Modifier.height(12.dp))

    if (q.isChoice) {
        q.options.forEach { opt ->
            val picked = if (q.multi) {
                answer.uppercase().contains(opt.label.uppercase())
            } else {
                answer.equals(opt.label, ignoreCase = true)
            }
            OptionRow(
                label = opt.label,
                content = opt.content,
                picked = picked,
                onClick = { onSelect(q.seq, opt.label) }
            )
            Spacer(Modifier.height(10.dp))
        }
    } else if (q.isMultiBlank) {
        // 多空填空：一空一个输入框。学生不必猜分隔符，服务端按空逐题判分
        // （存储时用全角「；」连接，提交时切成 blanks 数组，空串占位保留"漏答"事实）
        val parts = answer.split('；')
        repeat(q.blankCount) { i ->
            SoftFieldCard(
                label = "第 ${i + 1} 空 · 共 ${q.blankCount} 空",
                value = parts.getOrElse(i) { "" },
                onValueChange = { v ->
                    val cur = answer.split('；').toMutableList()
                    while (cur.size < q.blankCount) cur.add("")
                    cur[i] = v
                    onText(q.seq, cur.take(q.blankCount).joinToString("；").trimEnd('；'))
                },
                hint = if (i == 0) "按题面顺序逐空填写" else null,
                placeholder = "第 ${i + 1} 空",
                minLines = 1,
                mathPreview = true
            )
            Spacer(Modifier.height(8.dp))
        }
    } else {
        SoftFieldCard(
            label = if (q.isBlank) "填写答案" else "作答过程",
            value = answer,
            onValueChange = { onText(q.seq, it) },
            hint = if (q.isBlank) "按题面要求填写，如 2 或 x>1" else "写出关键步骤，老师按过程批改",
            placeholder = if (q.isBlank) "输入答案" else "写出你的解法与结论",
            minLines = if (q.isBlank) 2 else 5,
            mathPreview = true
        )
    }
}

/** 选项行：选中时品牌色描边 + 浅底，字母做成圆角色块。 */
@Composable
private fun OptionRow(label: String, content: String, picked: Boolean, onClick: () -> Unit) {
    val shape = RoundedCornerShape(14.dp)
    Row(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(if (picked) TesEduColors.BrandSoft else TesEduColors.Surface)
            .border(
                width = if (picked) 1.5.dp else 1.dp,
                color = if (picked) TesEduColors.Brand.copy(alpha = 0.55f) else TesEduColors.Divider,
                shape = shape
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(26.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(if (picked) TesEduColors.Brand else TesEduColors.FieldBg),
            contentAlignment = Alignment.Center
        ) {
            Text(
                label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Bold,
                color = if (picked) Color.White else TesEduColors.TextSecondary
            )
        }
        Spacer(Modifier.width(10.dp))
        MathText(
            content,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
    }
}

// ================================================================== 结果
@Composable
private fun ExamResultBody(
    ui: ExamUiState,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onSolution: (Int) -> Unit,
    onGraph: (Int) -> Unit
) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
    ) {
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) {
                Icon(Icons.Rounded.ArrowBack, contentDescription = "返回", tint = TesEduColors.Brand)
                Spacer(Modifier.width(4.dp))
                Text("作业", color = TesEduColors.Brand)
            }
            Spacer(Modifier.weight(1f))
            Icon(
                Icons.Rounded.Refresh,
                contentDescription = "刷新",
                tint = TesEduColors.TextMuted,
                modifier = Modifier
                    .size(20.dp)
                    .clickable { onRefresh() }
            )
        }

        if (ui.loading && ui.result == null) {
            LoadingBlock("正在取结果…")
        }

        ui.error?.let {
            SoftNotice(
                text = it,
                icon = Icons.Rounded.ErrorOutline,
                color = TesEduColors.Danger,
                bg = TesEduColors.DangerSoft
            )
            Spacer(Modifier.height(12.dp))
        }

        val r = ui.result
        if (r == null && !ui.loading) {
            Box(Modifier.fillMaxWidth().height(280.dp)) {
                EmptyState(
                    icon = Icons.Rounded.Assignment,
                    title = "还没有结果",
                    subtitle = "交卷后这里会出现逐题对错与解析"
                )
            }
        } else if (r != null) {
            ResultSummary(r = r, title = ui.title)
            Spacer(Modifier.height(14.dp))
            // M3-6：图谱按题下沉——每张题卡自带「本题知识图谱」，不再是整卷一个大图。
            // 学生看结果时关心的是「这一题卡在哪个知识点」，整卷图会把十道题的点混在一起。
            r.items.forEach { item ->
                ResultItemCard(
                    item = item,
                    question = ui.questions.firstOrNull { it.seq == item.seq },
                    solution = ui.solutions[item.seq],
                    onLoadSolution = { onSolution(item.seq) },
                    graph = ui.questionGraphs[item.seq],
                    onLoadGraph = { onGraph(item.seq) }
                )
                Spacer(Modifier.height(10.dp))
            }
        }
        Spacer(Modifier.height(28.dp))
    }
}

@Composable
private fun ResultSummary(r: ExamResultDto, title: String) {
    val shape = RoundedCornerShape(22.dp)
    Column(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(TesEduColors.BrandSofter)
            .padding(18.dp)
    ) {
        Text(
            title.ifBlank { r.title.ifBlank { "作业" } },
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.Bold,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                r.autoScoreText,
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.Brand
            )
            Spacer(Modifier.width(8.dp))
            Text(
                if (r.manualReviewedCount > 0) "题答对（含老师批改）" else "题答对（已自动判分）",
                style = MaterialTheme.typography.labelMedium,
                color = TesEduColors.TextSecondary,
                modifier = Modifier.padding(bottom = 6.dp)
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            InfoPill("共 ${r.totalCount} 题", TesEduColors.TextSecondary, TesEduColors.Surface)
            if (r.hasManualPending) {
                Spacer(Modifier.width(8.dp))
                InfoPill(
                    "${r.manualPendingCount} 题待人工",
                    TesEduColors.Amber,
                    TesEduColors.AmberSoft
                )
            }
            if (r.manualReviewedCount > 0) {
                Spacer(Modifier.width(8.dp))
                InfoPill(
                    "${r.manualReviewedCount} 题老师已批改",
                    TesEduColors.Success,
                    TesEduColors.SuccessSoft
                )
            }
        }
        if (r.hasManualPending) {
            Spacer(Modifier.height(10.dp))
            Text(
                "填空题与解答题由老师批改，出分前这里的正确题数只统计选择题。",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextSecondary
            )
        }
    }
}

@Composable
private fun ResultItemCard(
    item: com.tes.edu.data.remote.ExamResultItem,
    question: ExamQuestionView?,
    solution: ExamSolutionState?,
    onLoadSolution: () -> Unit,
    graph: QuestionGraphState?,
    onLoadGraph: () -> Unit
) {
    val (pillColor, pillBg, icon) = when {
        item.manualPending -> Triple(TesEduColors.Amber, TesEduColors.AmberSoft, Icons.Rounded.Schedule)
        item.correct -> Triple(
            TesEduColors.Success, TesEduColors.SuccessSoft, Icons.Rounded.CheckCircle
        )
        else -> Triple(TesEduColors.Danger, TesEduColors.DangerSoft, Icons.Rounded.ErrorOutline)
    }
    SoftCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = pillColor, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text(
                "第 ${item.seq} 题",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = TesEduColors.TextPrimary
            )
            // 人工判定的题要能被区分出来（机器判 vs 老师判），否则学生不知道谁给的结论
            if (item.reviewed) {
                Spacer(Modifier.width(6.dp))
                InfoPill("老师批改", TesEduColors.TextSecondary, TesEduColors.Surface)
            }
            Spacer(Modifier.weight(1f))
            InfoPill(item.verdictLabel, pillColor, pillBg)
        }
        // 题目原文 + 选项：只给对错看不出来这题在问什么，结果页要把题面还原出来
        question?.let { ResultQuestionBlock(it, item) }
        Spacer(Modifier.height(10.dp))
        MathText(
            "你的答案：${item.studentAnswer?.takeIf { it.isNotBlank() } ?: "（未作答）"}",
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextSecondary
        )
        // 待人工的题还没定对错，给"标准答案"会提前泄题，故只在已出结论时展示
        if (!item.manualPending) {
            item.correctAnswer?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(4.dp))
                MathText(
                    "正确答案：$it",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextPrimary
                )
            }
        } else {
            // 说明「不是判错，是判不了」，避免学生误以为自己被扣分
            item.reason?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(6.dp))
                MathText(
                    "待老师批改：$it",
                    style = MaterialTheme.typography.bodySmall,
                    color = TesEduColors.TextSecondary
                )
            }
        }
        // 老师判定时写的评语：让改判/判错有解释，而不是只甩一个结论
        if (item.reviewed) {
            item.teacherComment?.takeIf { it.isNotBlank() }?.let {
                Spacer(Modifier.height(10.dp))
                SoftQuote(
                    title = "老师评语",
                    text = it,
                    accent = TesEduColors.Success,
                    bg = TesEduColors.SuccessSoft
                )
            }
        }
        item.analysis?.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(10.dp))
            SoftQuote(
                title = "解析",
                text = it,
                accent = TesEduColors.Brand,
                bg = TesEduColors.BrandSofter
            )
        }
        SolutionSection(solution = solution, onLoad = onLoadSolution)
        QuestionGraphSection(state = graph, onLoad = onLoadGraph)
    }
}

/**
 * 结果页的题目原文 + 选项（只读）。
 *
 * 学生回看结果时首先要能看清「这题问什么、有哪些选项」，否则只有对错无从复盘。
 * 选择题把正确项标绿、学生错选项标红；填空/解答只还原题干。
 * 题干与选项取不到时（旧缓存/弱网）整块不渲染，退化为原来的答案+解析视图。
 */
@Composable
private fun ResultQuestionBlock(
    q: ExamQuestionView,
    item: com.tes.edu.data.remote.ExamResultItem
) {
    Spacer(Modifier.height(10.dp))
    if (q.stem.isNotBlank()) {
        MathText(
            q.stem,
            style = MaterialTheme.typography.bodyLarge,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(10.dp))
    }
    if (q.isChoice && q.options.isNotEmpty()) {
        // 只对选择题做选项高亮：填空题/解答题的标答是文本，不按字母解读
        val correctSet = labelsOf(item.correctAnswer)
        val selectedSet = labelsOf(item.studentAnswer)
        q.options.forEach { opt ->
            val label = opt.label.trim().uppercase()
            ResultOptionRow(
                label = opt.label,
                content = opt.content,
                correct = correctSet.contains(label),
                selected = selectedSet.contains(label)
            )
            Spacer(Modifier.height(8.dp))
        }
    }
}

/** 从答案串里抽选项字母（`AC` / `A,C` / `ABD` 都认）；只在选择题里用。 */
private fun labelsOf(answer: String?): Set<String> =
    answer.orEmpty().uppercase().filter { it in 'A'..'Z' }.map { it.toString() }.toSet()

/**
 * 结果页选项行（只读，无点击）。
 *
 * 绿=正确、红=学生选了但错、灰=既非正确也未被选；两者重合时按「正确」展示，
 * 因为学生最关心的是这题的正解长什么样。
 */
@Composable
private fun ResultOptionRow(
    label: String,
    content: String,
    correct: Boolean,
    selected: Boolean
) {
    val shape = RoundedCornerShape(14.dp)
    val bg = when {
        correct -> TesEduColors.SuccessSoft
        selected -> TesEduColors.DangerSoft
        else -> TesEduColors.Surface
    }
    val accent = when {
        correct -> TesEduColors.Success.copy(alpha = 0.55f)
        selected -> TesEduColors.Danger.copy(alpha = 0.55f)
        else -> TesEduColors.Divider
    }
    val labelBg = when {
        correct -> TesEduColors.Success
        selected -> TesEduColors.Danger
        else -> TesEduColors.FieldBg
    }
    val labelFg = if (correct || selected) Color.White else TesEduColors.TextSecondary
    Row(
        Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(bg)
            .border(if (correct || selected) 1.5.dp else 1.dp, accent, shape)
            .padding(horizontal = 12.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .size(26.dp)
                .clip(RoundedCornerShape(9.dp))
                .background(labelBg),
            contentAlignment = Alignment.Center
        ) {
            Text(
                label,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Bold,
                color = labelFg
            )
        }
        Spacer(Modifier.width(10.dp))
        MathText(
            content,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
        if (correct) {
            Spacer(Modifier.width(8.dp))
            Text(
                "正确",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.Success
            )
        } else if (selected) {
            Spacer(Modifier.width(8.dp))
            Text(
                "你选",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.Danger
            )
        }
    }
}

/**
 * M3-1/M3-2 按卷题的「看解析」。
 *
 * 只放在**结果页**：提交前不该有入口（服务端也会 403），所以这里不做「隐藏」判断，
 * 而是结果页天然只在交卷后出现。解析按需加载——题目多时不必一次生成整卷。
 */
@Composable
private fun SolutionSection(solution: ExamSolutionState?, onLoad: () -> Unit) {
    Spacer(Modifier.height(10.dp))
    when {
        solution == null -> SoftOutlinedButton(
            text = "看解题步骤",
            icon = Icons.Rounded.AutoStories,
            onClick = onLoad
        )

        solution.loading -> Row(verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(
                Modifier.size(14.dp),
                strokeWidth = 2.dp,
                color = TesEduColors.Brand
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "正在整理解题步骤…",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }

        solution.data?.solutionSteps != null -> {
            val steps = solution.data.solutionSteps.steps
            SoftQuote(
                title = "解题步骤",
                text = (listOf(solution.data.solutionSteps.answer.takeIf { it.isNotBlank() }
                    ?.let { "答案：$it" }) + steps.mapIndexed { i, s -> "${i + 1}. $s" })
                    .filterNotNull()
                    .joinToString("\n"),
                accent = TesEduColors.Brand,
                bg = TesEduColors.BrandSofter
            )
            if (solution.data.cached) {
                Spacer(Modifier.height(6.dp))
                Text(
                    "已缓存，不重复消耗生成额度",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }

        else -> {
            // 生成失败/未交卷：给原因 + 重试，不显示空白卡片
            Text(
                solution.error ?: "解析暂时不可用",
                style = MaterialTheme.typography.bodySmall,
                color = TesEduColors.TextSecondary
            )
            Spacer(Modifier.height(6.dp))
            SoftOutlinedButton(text = "重试", icon = Icons.Rounded.Refresh, onClick = onLoad)
        }
    }
}

// ======================================================= M3-6 逐题知识子图
/**
 * 结果页每题卡片自带一个「本题知识图谱」：默认收起，**展开才拉那道题的子图**。
 *
 * 为什么不做成整卷图谱按钮：学生看「查看结果」时关心的是「这一题卡在哪个点」，
 * 整卷图把十道题的知识点混在一起，反而定位不到。按题懒加载还顺带省了流量——
 * 十道题的卷子不会一次发十个请求，失败重试也只影响这一张卡片。
 */
@Composable
private fun QuestionGraphSection(
    state: QuestionGraphState?,
    onLoad: () -> Unit
) {
    var expanded by remember { mutableStateOf(false) }
    Spacer(Modifier.height(10.dp))
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(TesEduColors.BrandSofter)
            .clickable {
                expanded = !expanded
                // 首次展开、或上次失败后才请求；已成功的不重复打接口
                if (expanded && state?.data == null && state?.loading != true) onLoad()
            }
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            Icons.Rounded.AccountTree,
            contentDescription = null,
            tint = TesEduColors.Brand,
            modifier = Modifier.size(16.dp)
        )
        Spacer(Modifier.width(8.dp))
        Text(
            "本题知识图谱",
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.SemiBold,
            color = TesEduColors.Brand,
            modifier = Modifier.weight(1f)
        )
        val nodeCount = state?.data?.nodes?.size ?: 0
        if (nodeCount > 0) {
            InfoPill("$nodeCount 个知识点", TesEduColors.TextSecondary, TesEduColors.Surface)
            Spacer(Modifier.width(6.dp))
        }
        Text(
            if (expanded) "收起" else "展开",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
    }

    if (expanded) {
        Spacer(Modifier.height(10.dp))
        val error = state?.error
        val data = state?.data
        when {
            state == null || state.loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(
                    Modifier.size(14.dp), strokeWidth = 2.dp, color = TesEduColors.Brand
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "正在整理本题知识点…",
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }

            error != null -> {
                Text(error, style = MaterialTheme.typography.bodySmall, color = TesEduColors.Danger)
                Spacer(Modifier.height(6.dp))
                SoftOutlinedButton(text = "重试", icon = Icons.Rounded.Refresh, onClick = onLoad)
            }

            data != null -> QuestionGraphContent(data)
        }
    }
}

/** 三层结构：前置 → 本题考点 → 后继；节点带掌握度配色带，与图谱页同一口径。 */
@Composable
private fun QuestionGraphContent(dto: QuestionGraphDto) {
    if (!dto.hasGraph) {
        Text(
            dto.note?.takeIf { it.isNotBlank() } ?: "本题暂未标注知识点",
            style = MaterialTheme.typography.bodySmall,
            color = TesEduColors.TextSecondary
        )
    } else {
        GraphLayer(
            title = "先学 · 前置知识",
            nodes = dto.prereqNodes(),
            accent = TesEduColors.Amber,
            bg = TesEduColors.AmberSoft
        )
        if (dto.coreNodes().isNotEmpty()) GraphFlowLine()
        GraphLayer(
            title = "本题考点",
            nodes = dto.coreNodes(),
            accent = TesEduColors.Brand,
            bg = TesEduColors.BrandSoft
        )
        if (dto.successorNodes().isNotEmpty()) GraphFlowLine()
        GraphLayer(
            title = "接着学 · 后续知识",
            nodes = dto.successorNodes(),
            accent = TesEduColors.Mint,
            bg = TesEduColors.MintSoft
        )
        if (dto.unresolvedKps.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text(
                "另有 ${dto.unresolvedKps.size} 个标签未匹配知识点",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
        }
    }
}

@Composable
private fun GraphLayer(
    title: String,
    nodes: List<GraphNode>,
    accent: Color,
    bg: Color
) {
    if (nodes.isEmpty()) return
    Text(
        title,
        style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.SemiBold,
        color = accent
    )
    Spacer(Modifier.height(6.dp))
    nodes.forEach { n ->
        Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(12.dp))
                .background(bg)
                .padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    n.title?.takeIf { it.isNotBlank() } ?: n.code,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = TesEduColors.TextPrimary
                )
                // 副行只显示中文（章·节），不再暴露 DAG 编码（如 MA-B2-C4-S2-K04）
                val sub = listOfNotNull(
                    n.chapter?.takeIf { it.isNotBlank() },
                    n.section?.takeIf { it.isNotBlank() }
                ).joinToString(" · ").ifBlank { n.code }
                if (sub.isNotBlank()) {
                    Text(sub, style = MaterialTheme.typography.labelSmall, color = TesEduColors.TextSecondary)
                }
                n.mastery?.let { m ->
                    Text(
                        "掌握度 ${(m * 100).roundToInt()}%",
                        style = MaterialTheme.typography.labelSmall,
                        color = TesEduColors.TextMuted
                    )
                }
            }
            Spacer(Modifier.width(6.dp))
            InfoPill(bandLabel(n.band), bandColor(n.band), TesEduColors.Surface)
        }
        Spacer(Modifier.height(6.dp))
    }
}

/** 学习流向的连接线：前置在上、本题居中、后继在下（from=前置、to=后置）。 */
@Composable
private fun GraphFlowLine() {
    Row(
        Modifier.fillMaxWidth().padding(start = 12.dp, top = 2.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(width = 2.dp, height = 14.dp).background(TesEduColors.Divider))
        Spacer(Modifier.width(8.dp))
        Text(
            "↓ 学习流向",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted
        )
    }
}

private fun bandLabel(band: String): String = when (band) {
    GraphNode.BAND_STRONG -> "已掌握"
    GraphNode.BAND_MEDIUM -> "巩固中"
    GraphNode.BAND_WEAK -> "薄弱"
    else -> "未学过"
}

private fun bandColor(band: String): Color = when (band) {
    GraphNode.BAND_STRONG -> TesEduColors.Success
    GraphNode.BAND_MEDIUM -> TesEduColors.Brand
    GraphNode.BAND_WEAK -> TesEduColors.Danger
    else -> TesEduColors.TextSecondary
}

// ================================================================== 零件
@Composable
private fun LoadingBlock(text: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 26.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(18.dp),
            strokeWidth = 2.dp,
            color = TesEduColors.Brand
        )
        Spacer(Modifier.width(10.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = TesEduColors.TextMuted)
    }
}

/** ISO-8601 -> `MM-dd`；解析不了就原样给前 10 位，不为了一个日期把页面搞崩。 */
private fun shortDate(iso: String): String {
    val d = iso.take(10)
    return if (d.length == 10) d.substring(5) else iso
}
