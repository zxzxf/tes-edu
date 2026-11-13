package com.tes.edu

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.unit.sp
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.tes.edu.data.remote.AppContextProvider
import com.tes.edu.data.remote.AuthBus
import com.tes.edu.data.remote.SessionStore
import com.tes.edu.ui.screens.CameraScreen
import com.tes.edu.ui.screens.ExamScreen
import com.tes.edu.ui.screens.ExplainScreen
import com.tes.edu.ui.screens.GraphScreen
import com.tes.edu.ui.screens.InputScreen
import com.tes.edu.ui.screens.ReviewScreen
import com.tes.edu.ui.screens.Screen
import com.tes.edu.ui.screens.SimilarScreen
import com.tes.edu.ui.screens.LoginModal
import com.tes.edu.ui.screens.LoginScreen
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.ui.theme.TesEduTheme
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.tes.edu.util.CrashReporter

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppContextProvider.init(applicationContext)
        CrashReporter.init(applicationContext)
        setContent {
            TesEduTheme {
                AppRoot()
            }
        }
        // 若有上次崩溃，启动后弹窗展示堆栈，可一键复制到剪贴板（无需 adb）
        CrashReporter.consumePendingCrash()?.let { report ->
            Handler(Looper.getMainLooper()).post { showCrashDialog(report) }
        }
    }

    private fun showCrashDialog(report: String) {
        val scroll = ScrollView(this)
        val tv = TextView(this).apply {
            text = report
            setTextIsSelectable(true)
            textSize = 11f
            setPadding(24, 24, 24, 24)
        }
        scroll.addView(tv)
        AlertDialog.Builder(this)
            .setTitle("上一 session 捕获到崩溃（可复制发给开发）")
            .setView(scroll)
            .setPositiveButton("复制到剪贴板") { _, _ ->
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("crash", report))
                Toast.makeText(this, "已复制，可粘贴发给开发", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton("关闭") { d, _ -> d.dismiss() }
            .show()
    }
}

@Composable
fun AppRoot() {
    // 冷启动是否已有登录态（有 token 即视为已登录）
    var loggedIn by remember {
        mutableStateOf(!SessionStore(AppContextProvider.context).token.isNullOrBlank())
    }
    // 登录态失效（401 触发）：浮登录遮罩
    val expired by AuthBus.expired.collectAsState()
    // 重新登录成功自增：重进当前页让 LaunchedEffect 重新拉数
    val restored by AuthBus.sessionRestored.collectAsState()

    // —— 未登录：整页登录 ——
    if (!loggedIn) {
        LoginScreen(onSuccess = { loggedIn = true })
        return
    }

    val navController: NavHostController = rememberNavController()
    val items = listOf(
        Screen.Exam,
        Screen.Input, Screen.Camera, Screen.Explain,
        Screen.Similar, Screen.Graph, Screen.Review
    )
    // 底部导航必须知道「当前在哪一页」，否则所有 item 都画成未选中（旧实现即此 bug）
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    // 页内联动跳转（触类「再练这道」、图谱两个按钮）：复用底部导航的
    // 单实例 + 保状态语义，避免切页堆出多份返回栈。
    val go: (String) -> Unit = { route ->
        navController.navigate(route) {
            popUpTo(navController.graph.startDestinationId) { saveState = true }
            launchSingleTop = true
            restoreState = true
        }
    }

    // 重新登录成功后，重新进入当前页（restoreState=false 强制重组，触发数据重拉）
    LaunchedEffect(restored) {
        if (restored > 0) {
            val route = navController.currentDestination?.route
            if (route != null) {
                navController.navigate(route) {
                    popUpTo(navController.graph.startDestinationId) { saveState = true }
                    launchSingleTop = true
                    restoreState = false
                }
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        Scaffold(
            containerColor = TesEduColors.Bg,
            bottomBar = {
                TesBottomBar(
                    items = items,
                    currentRoute = currentRoute,
                    onSelect = { screen -> go(screen.route) }
                )
            }
        ) { padding ->
            NavHost(
                navController,
                // 作业闭环是这一版的主链路：冷启动直接落在「作业」，
                // 没有下发卷时该页给空态并引导去错题本。
                startDestination = Screen.Exam.route,
                modifier = Modifier.padding(padding)
            ) {
                // 结果页的图谱已在题卡内就地展开，作业页不再需要跨页跳转
                composable(Screen.Exam.route) { ExamScreen() }
                composable(Screen.Input.route) { InputScreen() }
                composable(Screen.Camera.route) { CameraScreen(onNavigate = go) }
                composable(Screen.Explain.route) { ExplainScreen() }
                composable(Screen.Similar.route) { SimilarScreen(onNavigate = go) }
                composable(Screen.Graph.route) { GraphScreen(onNavigate = go) }
                composable(Screen.Review.route) { ReviewScreen(onNavigate = go) }
            }
        }

        // —— 登录过期：浮登录遮罩（强制重登录）——
        if (expired) {
            LoginModal(
                // 登录成功后过期标记已在 LoginManager.login 内清除，这里无需额外处理
                onSuccess = {},
                onDismiss = {
                    // 主动退回整页登录：清过期标记 + 收起主界面
                    AuthBus.onReloginSuccess()
                    loggedIn = false
                }
            )
        }
    }
}

/**
 * 自定义底部导航：选中项用「浅色圆角块 + 品牌色图标/文字」表达，
 * 比 Material 默认的 pill 指示器更轻、更贴近学生端产品的审美。
 */
@Composable
private fun TesBottomBar(
    items: List<Screen>,
    currentRoute: String?,
    onSelect: (Screen) -> Unit
) {
    Surface(color = TesEduColors.Surface, shadowElevation = 18.dp) {
        Row(
            Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 6.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            items.forEach { screen ->
                val selected = currentRoute == screen.route
                Column(
                    Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(14.dp))
                        .clickable { onSelect(screen) }
                        .padding(vertical = 4.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Box(
                        Modifier
                            .size(36.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(if (selected) TesEduColors.BrandSoft else Color.Transparent),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            screen.icon,
                            contentDescription = screen.label,
                            tint = if (selected) TesEduColors.Brand else TesEduColors.TextMuted,
                            modifier = Modifier.size(21.dp)
                        )
                    }
                    Spacer(Modifier.height(3.dp))
                    Text(
                        screen.label,
                        fontSize = 10.sp,
                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                        color = if (selected) TesEduColors.Brand else TesEduColors.TextMuted
                    )
                }
            }
        }
    }
}
