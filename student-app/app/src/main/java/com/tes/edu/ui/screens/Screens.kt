package com.tes.edu.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.Bookmark
import androidx.compose.material.icons.filled.AccountTree
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.MenuBook
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp

// 六大能力页定义（P0 基础壳：底部导航 + 占位页，后续填充真实交互）
sealed class Screen(val route: String, val label: String, val icon: ImageVector) {
    object Input : Screen("input", "输入", Icons.Filled.Edit)
    object Camera : Screen("camera", "拍照", Icons.Filled.PhotoCamera)
    object Explain : Screen("explain", "讲解", Icons.Filled.MenuBook)
    object Similar : Screen("similar", "触类", Icons.Filled.AutoStories)
    object Graph : Screen("graph", "图谱", Icons.Filled.AccountTree)
    object Review : Screen("review", "背诵", Icons.Filled.Bookmark)
}

@Composable fun InputScreen() = Placeholder("文本 / 语音输入")
@Composable fun CameraScreen() = Placeholder("拍照识题")
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
