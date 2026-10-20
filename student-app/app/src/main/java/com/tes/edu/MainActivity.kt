package com.tes.edu

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.tes.edu.ui.screens.*

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                AppRoot()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppRoot() {
    val navController: NavHostController = rememberNavController()
    val items = listOf(
        Screen.Input, Screen.Camera, Screen.Explain,
        Screen.Similar, Screen.Graph, Screen.Review
    )
    Scaffold(
        bottomBar = {
            NavigationBar {
                items.forEach { screen ->
                    NavigationBarItem(
                        selected = false,
                        onClick = { navController.navigate(screen.route) },
                        icon = { Icon(screen.icon, contentDescription = screen.label) },
                        label = { Text(screen.label) }
                    )
                }
            }
        }
    ) { padding ->
        NavHost(
            navController,
            startDestination = Screen.Input.route,
            Modifier.padding(padding)
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
