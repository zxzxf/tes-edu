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
import androidx.compose.runtime.getValue
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
import com.tes.edu.ui.screens.CameraScreen
import com.tes.edu.ui.screens.ExplainScreen
import com.tes.edu.ui.screens.GraphScreen
import com.tes.edu.ui.screens.InputScreen
import com.tes.edu.ui.screens.ReviewScreen
import com.tes.edu.ui.screens.Screen
import com.tes.edu.ui.screens.SimilarScreen
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.ui.theme.TesEduTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppContextProvider.init(applicationContext)
        setContent {
            TesEduTheme {
                AppRoot()
            }
        }
    }
}

@Composable
fun AppRoot() {
    val navController: NavHostController = rememberNavController()
    val items = listOf(
        Screen.Input, Screen.Camera, Screen.Explain,
        Screen.Similar, Screen.Graph, Screen.Review
    )
    // 底部导航必须知道「当前在哪一页」，否则所有 item 都画成未选中（旧实现即此 bug）
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    Scaffold(
        containerColor = TesEduColors.Bg,
        bottomBar = {
            TesBottomBar(
                items = items,
                currentRoute = currentRoute,
                onSelect = { screen ->
                    navController.navigate(screen.route) {
                        // 单实例 + 保留各页状态，反复切页不堆栈
                        popUpTo(navController.graph.startDestinationId) { saveState = true }
                        launchSingleTop = true
                        restoreState = true
                    }
                }
            )
        }
    ) { padding ->
        NavHost(
            navController,
            startDestination = Screen.Input.route,
            modifier = Modifier.padding(padding)
        ) {
            composable(Screen.Input.route) { InputScreen() }
            composable(Screen.Camera.route) { CameraScreen() }
            composable(Screen.Explain.route) { ExplainScreen() }
            composable(Screen.Similar.route) { SimilarScreen() }
            composable(Screen.Graph.route) { GraphScreen() }
            composable(Screen.Review.route) { ReviewScreen() }
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
