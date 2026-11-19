package com.tes.edu.ui.screens

import androidx.compose.foundation.background
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tes.edu.data.remote.ApiClient
import com.tes.edu.data.remote.PushMessageItem
import com.tes.edu.state.AppBridge
import com.tes.edu.state.PushStore
import com.tes.edu.ui.components.TesHero
import com.tes.edu.ui.theme.TesEduColors
import kotlinx.coroutines.launch

/**
 * 消息中心（推送中心 P4）：收件箱列表 + 主题 Tab（带分主题未读角标）+ 点击跳转。
 *
 * 设计口径（计划 v1.5）：
 * - 数据权威在服务端：进页全量拉 /push/messages；PushStore 长轮询发现新消息
 *   时 tick 自增，本页 collectAsState 感知后重拉（不做本地增量合并，简单可靠）。
 * - 点击行为 = 阅读 + 按 payload.type 跳转 + 标已读；「全部已读」按当前 Tab 范围。
 * - 推送只当提醒：正文里的成绩/题目细节不预判，跳转后走各页自己的接口拉详情。
 */

private data class MsgTab(val code: String?, val label: String)

private val MSG_TABS = listOf(
    MsgTab(null, "全部"),
    MsgTab("homework", "作业"),
    MsgTab("grade", "成绩"),
    MsgTab("wrongbook", "错题"),
    MsgTab("system", "系统"),
)

@Composable
fun MessagesScreen(onNavigate: (String) -> Unit = {}) {
    val scope = rememberCoroutineScope()
    var tab by remember { mutableStateOf<MsgTab?>(null) } // null=加载中占位
    var items by remember { mutableStateOf<List<PushMessageItem>>(emptyList()) }
    var topicUnread by remember { mutableStateOf<Map<String, Int>>(emptyMap()) }
    var unreadTotal by remember { mutableStateOf(0) }
    var error by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(true) }
    val tick by PushStore.newMsgTick.collectAsState()

    val sel = tab ?: MSG_TABS[0]

    suspend fun load() {
        try {
            val data = ApiClient.push.messages(topicCode = sel.code, size = 50)
            items = data.items
            topicUnread = data.unreadByTopic
            unreadTotal = data.unreadTotal
            error = ""
        } catch (e: Exception) {
            error = "消息加载失败：${e.message ?: "网络异常"}"
        } finally {
            loading = false
        }
    }

    // 进页 / 切 Tab / 长轮询发现新消息 → 重拉
    LaunchedEffect(sel, tick) {
        loading = true
        load()
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 18.dp)
    ) {
        Spacer(Modifier.height(14.dp))
        TesHero(
            title = "消息",
            subtitle = "作业、成绩与系统通知都在这",
            badge = if (unreadTotal > 0) "未读 $unreadTotal" else "全部已读"
        )

        Spacer(Modifier.height(14.dp))
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            MSG_TABS.forEach { t ->
                val on = sel.code == t.code
                val n = if (t.code == null) unreadTotal else topicUnread[t.code] ?: 0
                Box(
                    Modifier
                        .clip(RoundedCornerShape(999.dp))
                        .background(if (on) TesEduColors.BrandSoft else TesEduColors.FieldBg)
                        .clickable { tab = t }
                        .padding(horizontal = 12.dp, vertical = 6.dp)
                ) {
                    Text(
                        text = if (n > 0) "${t.label}($n)" else t.label,
                        fontSize = 12.sp,
                        fontWeight = if (on) FontWeight.Bold else FontWeight.Medium,
                        color = if (on) TesEduColors.BrandDeep else TesEduColors.TextSecondary
                    )
                }
            }
        }

        if (error.isNotBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(error, fontSize = 12.sp, color = TesEduColors.Danger)
        }

        if (!loading && items.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text(
                "全部已读",
                fontSize = 12.5.sp,
                color = TesEduColors.Brand,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable {
                        scope.launch {
                            runCatching { PushStore.markAllRead(sel.code) }
                            load()
                        }
                    }
                    .padding(vertical = 2.dp)
            )
        }

        Spacer(Modifier.height(12.dp))
        if (loading && items.isEmpty()) {
            Text("加载中…", fontSize = 13.sp, color = TesEduColors.TextMuted)
        } else if (items.isEmpty() && error.isBlank()) {
            Column(
                Modifier.fillMaxWidth().padding(vertical = 48.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text("暂时没有消息", fontSize = 15.sp, color = TesEduColors.TextSecondary)
                Spacer(Modifier.height(6.dp))
                Text("老师下发作业、批改完成后会自动通知到这里", fontSize = 12.sp, color = TesEduColors.TextMuted)
            }
        } else {
            items.forEach { m ->
                MessageCard(
                    msg = m,
                    onClick = {
                        if (!m.read) scope.launch { runCatching { PushStore.markRead(m.msgId) } }
                        routeByPayload(m, onNavigate)
                    }
                )
                Spacer(Modifier.height(10.dp))
            }
        }
        Spacer(Modifier.height(24.dp))
    }
}

/** 点击跳转：payload.type 优先，缺省按主题兜底；无跳转语义就留在本页。 */
private fun routeByPayload(m: PushMessageItem, onNavigate: (String) -> Unit) {
    val type = (m.payload?.get("type") as? String) ?: m.topicCode
    when (type) {
        "homework" -> {
            // 作业消息带 assignment_id：进作业 tab（AppBridge 后续可扩展定位到卷）
            val id = (m.payload?.get("assignment_id") as? Double)?.toLong()
            if (id != null && id > 0) AppBridge.postPaperGraph(id, m.payload?.get("paper_title") as? String)
            onNavigate(Screen.Exam.route)
        }
        "grade" -> onNavigate(Screen.Exam.route)        // 成绩在作业结果里看
        "wrongbook" -> onNavigate(Screen.Review.route)  // 错题复盘提醒进错题本
        "practice" -> onNavigate(Screen.Input.route)
        // system/其他：留在消息页即完成阅读
    }
}

@Composable
private fun MessageCard(msg: PushMessageItem, onClick: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(TesEduColors.Surface)
            .clickable(onClick = onClick)
            .padding(14.dp),
        verticalAlignment = Alignment.Top
    ) {
        // 未读圆点
        Box(
            Modifier
                .padding(top = 6.dp)
                .size(8.dp)
                .clip(RoundedCornerShape(999.dp))
                .background(if (msg.read) Color.Transparent else TesEduColors.BrandViolet)
        )
        Spacer(Modifier.size(10.dp))
        Column(Modifier.weight(1f)) {
            Text(
                msg.title,
                fontSize = 14.5.sp,
                fontWeight = if (msg.read) FontWeight.Medium else FontWeight.Bold,
                color = TesEduColors.TextPrimary
            )
            Spacer(Modifier.height(3.dp))
            Text(
                msg.body,
                fontSize = 12.5.sp,
                color = TesEduColors.TextSecondary,
                maxLines = 2
            )
            Spacer(Modifier.height(6.dp))
            Text(
                topicLabel(msg.topicCode) + " · " + fmtTime(msg.createdAtMs),
                fontSize = 11.sp,
                color = TesEduColors.TextMuted
            )
        }
    }
}

private fun topicLabel(code: String): String = when (code) {
    "homework" -> "作业"
    "grade" -> "成绩"
    "wrongbook" -> "错题"
    "system" -> "系统"
    "practice" -> "练习"
    else -> code
}

private fun fmtTime(ms: Long): String {
    if (ms <= 0) return ""
    val diff = System.currentTimeMillis() - ms
    return when {
        diff < 60_000 -> "刚刚"
        diff < 3_600_000 -> "${diff / 60_000} 分钟前"
        diff < 86_400_000 -> "${diff / 3_600_000} 小时前"
        diff < 7 * 86_400_000 -> "${diff / 86_400_000} 天前"
        else -> {
            val d = java.util.Date(ms)
            String.format(
                java.util.Locale.CHINA, "%t月%td日", d, d
            )
        }
    }
}
