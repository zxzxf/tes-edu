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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material.icons.rounded.Visibility
import androidx.compose.material.icons.rounded.VisibilityOff
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tes.edu.data.remote.LoginManager
import com.tes.edu.ui.components.GradientButton
import com.tes.edu.ui.components.SoftNotice
import com.tes.edu.ui.theme.TesEduColors
import kotlinx.coroutines.launch

/**
 * 登录页与过期登录遮罩共用的表单。
 *
 * 两种入口：
 * - 整页 [LoginScreen]：冷启动无 token 时进入；成功后回调 [onSuccess] 进入主界面。
 * - 遮罩 [LoginModal]：登录过期（401）时浮在现有界面之上；成功后回调 [onSuccess]
 *   关闭遮罩并刷新页面。
 *
 * 登录方式：账号密码（本次实现）+ 微信小程序扫码（占位，待后端接入 ticket 生成与轮询）。
 */
@Composable
private fun LoginForm(
    compact: Boolean,
    onSuccess: () -> Unit
) {
    val scope = rememberCoroutineScope()
    var mode by remember { mutableStateOf(LoginMode.PWD) }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPwd by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // —— 登录方式切换 tab ——
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(TesEduColors.FieldBg)
            .padding(4.dp)
    ) {
        LoginTab(
            text = "账号密码登录",
            selected = mode == LoginMode.PWD,
            modifier = Modifier.weight(1f)
        ) { mode = LoginMode.PWD }
        LoginTab(
            text = "微信扫码登录",
            selected = mode == LoginMode.WX,
            modifier = Modifier.weight(1f)
        ) { mode = LoginMode.WX }
    }

    Spacer(Modifier.height(18.dp))

    if (mode == LoginMode.PWD) {
        // 用户名
        LabeledField(
            label = "用户名",
            value = username,
            onValueChange = { username = it },
            placeholder = "请输入用户名",
            leadingIcon = Icons.Rounded.Person,
            enabled = !loading
        )
        Spacer(Modifier.height(14.dp))
        // 密码（带可见性切换）
        LabeledField(
            label = "密码",
            value = password,
            onValueChange = { password = it },
            placeholder = "请输入密码",
            leadingIcon = Icons.Rounded.Lock,
            enabled = !loading,
            isPassword = true,
            showPassword = showPwd,
            onTogglePassword = { showPwd = !showPwd },
            keyboardType = KeyboardType.Password
        )

        Spacer(Modifier.height(20.dp))
        GradientButton(
            text = if (loading) "登录中…" else if (compact) "重新登录" else "登 录",
            icon = Icons.Rounded.Lock,
            enabled = !loading
        ) {
            error = null
            if (username.isBlank() || password.isBlank()) {
                error = "请输入用户名和密码"
                return@GradientButton
            }
            loading = true
            scope.launch {
                val ok = LoginManager.login(username, password)
                loading = false
                if (ok) onSuccess() else error = "登录失败，请检查用户名、密码或网络"
            }
        }

        error?.let {
            Spacer(Modifier.height(14.dp))
            SoftNotice(
                text = it,
                icon = Icons.Rounded.Lock,
                color = TesEduColors.Danger,
                bg = TesEduColors.DangerSoft
            )
        }

        // 演示账号一键填充（开发/联调用，接真实用户库后移除）
        Spacer(Modifier.height(16.dp))
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "演示账号 ${LoginManager.DEV_USERNAME} / ${LoginManager.DEV_PASSWORD}",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted
            )
            Spacer(Modifier.weight(1f))
            Text(
                "一键填充",
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.Brand,
                modifier = Modifier
                    .clickable(enabled = !loading) {
                        username = LoginManager.DEV_USERNAME
                        password = LoginManager.DEV_PASSWORD
                    }
            )
        }
    } else {
        // 微信小程序扫码登录：占位，待后端接入
        WxQrPlaceholder()
    }

    Spacer(Modifier.height(if (compact) 14.dp else 22.dp))
    Text(
        "与「学生 App」使用同一套账号",
        style = MaterialTheme.typography.labelSmall,
        color = TesEduColors.TextMuted,
        textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth()
    )
}

/** 表单顶部两个登录方式 tab。 */
@Composable
private fun LoginTab(text: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(11.dp))
            .background(if (selected) TesEduColors.Surface else Color.Transparent)
            .border(
                if (selected) 1.dp else 0.dp,
                TesEduColors.Brand.copy(alpha = 0.3f),
                RoundedCornerShape(11.dp)
            )
            .clickable(onClick = onClick)
            .padding(vertical = 9.dp),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
            color = if (selected) TesEduColors.Brand else TesEduColors.TextMuted
        )
    }
}

/** 标签 + 输入框（聚焦描边、无边框填充，与全站 SoftFieldCard 观感一致）。 */
@Composable
private fun LabeledField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    leadingIcon: androidx.compose.ui.graphics.vector.ImageVector,
    enabled: Boolean,
    isPassword: Boolean = false,
    showPassword: Boolean = false,
    onTogglePassword: (() -> Unit)? = null,
    keyboardType: KeyboardType = KeyboardType.Text
) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            label,
            style = MaterialTheme.typography.titleSmall,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(8.dp))
        TextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier.fillMaxWidth(),
            enabled = enabled,
            placeholder = {
                Text(
                    placeholder,
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextMuted
                )
            },
            leadingIcon = {
                Box(
                    Modifier
                        .size(26.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(TesEduColors.Brand.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        leadingIcon,
                        contentDescription = null,
                        tint = TesEduColors.Brand,
                        modifier = Modifier.size(15.dp)
                    )
                }
            },
            trailingIcon = if (isPassword && onTogglePassword != null) {
                {
                    Icon(
                        if (showPassword) Icons.Rounded.VisibilityOff else Icons.Rounded.Visibility,
                        contentDescription = if (showPassword) "隐藏密码" else "显示密码",
                        tint = TesEduColors.TextMuted,
                        modifier = Modifier
                            .size(20.dp)
                            .clickable(onClick = onTogglePassword)
                    )
                }
            } else null,
            visualTransformation = if (isPassword && !showPassword)
                PasswordVisualTransformation() else VisualTransformation.None,
            keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
            shape = RoundedCornerShape(16.dp),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = TesEduColors.FieldBg,
                unfocusedContainerColor = TesEduColors.FieldBg,
                disabledContainerColor = TesEduColors.FieldBg,
                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
                disabledIndicatorColor = Color.Transparent,
                cursorColor = TesEduColors.Brand,
                focusedTextColor = TesEduColors.TextPrimary,
                unfocusedTextColor = TesEduColors.TextPrimary,
                focusedPlaceholderColor = TesEduColors.TextMuted,
                unfocusedPlaceholderColor = TesEduColors.TextMuted
            )
        )
    }
}

/** 微信小程序扫码占位：二维码虚框 + 待实现角标（后端接入 ticket 生成与轮询后替换）。 */
@Composable
private fun WxQrPlaceholder() {
    Column(
        Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            Modifier
                .size(168.dp)
                .clip(RoundedCornerShape(18.dp))
                .border(2.dp, TesEduColors.Divider, RoundedCornerShape(18.dp))
                .background(TesEduColors.FieldBg),
            contentAlignment = Alignment.Center
        ) {
            Text("📱", fontSize = 46.sp)
            Box(
                Modifier
                    .align(Alignment.TopEnd)
                    .padding(8.dp)
                    .clip(RoundedCornerShape(50))
                    .background(TesEduColors.Amber)
                    .padding(horizontal = 8.dp, vertical = 3.dp)
            ) {
                Text(
                    "待实现",
                    color = Color.White,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold
                )
            }
        }
        Spacer(Modifier.height(16.dp))
        Text(
            "请使用「Tes Edu 微信小程序」扫描二维码登录",
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.SemiBold,
            color = TesEduColors.TextPrimary,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "微信小程序扫码登录正在开发中，敬请期待。",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.TextMuted,
            textAlign = TextAlign.Center
        )
    }
}

/** 整页登录：冷启动无 token 时进入。 */
@Composable
fun LoginScreen(onSuccess: () -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .background(TesEduColors.Bg)
    ) {
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Spacer(Modifier.height(72.dp))
            // 品牌徽标
            Box(
                Modifier
                    .size(64.dp)
                    .clip(CircleShape)
                    .background(
                        Brush.linearGradient(
                            listOf(TesEduColors.Brand, TesEduColors.BrandDeep, TesEduColors.BrandViolet)
                        )
                    ),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    "T",
                    color = Color.White,
                    fontSize = 30.sp,
                    fontWeight = FontWeight.Black
                )
            }
            Spacer(Modifier.height(14.dp))
            Text(
                "Tes Edu",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                color = TesEduColors.TextPrimary
            )
            Spacer(Modifier.height(40.dp))

            Surface(
                Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(24.dp),
                color = TesEduColors.Surface,
                shadowElevation = 6.dp,
                border = androidx.compose.foundation.BorderStroke(
                    1.dp,
                    TesEduColors.Divider.copy(alpha = 0.7f)
                )
            ) {
                Column(Modifier.padding(22.dp)) {
                    Text(
                        "同学，你好 👋",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = TesEduColors.TextPrimary
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "登录以进入你的学习工作台",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TesEduColors.TextSecondary
                    )
                    Spacer(Modifier.height(22.dp))
                    LoginForm(compact = false, onSuccess = onSuccess)
                }
            }
            Spacer(Modifier.height(28.dp))
        }
    }
}

/**
 * 登录过期遮罩：401 时浮在现有界面之上，强制重新登录。
 *
 * 不可点遮罩外关闭——必须登录或主动选择"返回登录页"。成功后回调 [onSuccess] 关闭遮罩；
 * [onDismiss] 用于"返回登录页"按钮（退到整页登录）。
 */
@Composable
fun LoginModal(onSuccess: () -> Unit, onDismiss: () -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .background(Color(0x8C1F1B2E)) // 深色半透明遮罩（与 web 端一致）
            .clickable(enabled = false) { }, // 拦截点击，避免穿透到下层界面
        contentAlignment = Alignment.Center
    ) {
        Surface(
            Modifier
                .width(360.dp)
                .padding(20.dp),
            shape = RoundedCornerShape(24.dp),
            color = TesEduColors.Surface,
            shadowElevation = 18.dp
        ) {
            Column(
                Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                // 浮动锁形徽标
                Box(
                    Modifier
                        .size(60.dp)
                        .clip(CircleShape)
                        .background(
                            Brush.linearGradient(
                                listOf(TesEduColors.Brand, TesEduColors.BrandViolet)
                            )
                        ),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Rounded.Lock,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(28.dp)
                    )
                }
                Spacer(Modifier.height(14.dp))
                Text(
                    "登录已过期",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                    color = TesEduColors.TextPrimary
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "登录状态已失效，重新登录后即可继续刚才的学习，进度不会丢失",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TesEduColors.TextSecondary,
                    textAlign = TextAlign.Center
                )
                Spacer(Modifier.height(22.dp))
                LoginForm(compact = true, onSuccess = onSuccess)
                Spacer(Modifier.height(10.dp))
                // 次级：暂不登录，退回整页登录
                Box(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .border(1.5.dp, TesEduColors.Divider, RoundedCornerShape(14.dp))
                        .clickable(onClick = onDismiss)
                        .padding(vertical = 12.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        "暂不登录，返回登录页",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = TesEduColors.TextSecondary
                    )
                }
            }
        }
    }
}

private enum class LoginMode { PWD, WX }
