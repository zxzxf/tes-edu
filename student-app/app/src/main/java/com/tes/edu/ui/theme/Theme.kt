package com.tes.edu.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 学生端设计系统。
 *
 * 定位：面向高中生的学习工具 —— 有冲劲、有热血，但不幼稚、不廉价。
 * 主色用「烈焰橙 → 热情玫红」渐变（拼搏 / 青春 / 激情），与 web 端同一套暖色语言保持一致；
 * 辅色薄荷青（正向反馈），强调色拼搏金（提醒 / 奖励），背景是暖白，卡片纯白 + 大圆角。
 *
 * 所有页面统一从这里取色，不要在页面里散落硬编码色值。
 */
object TesEduColors {
    // —— 品牌主色（烈焰橙 → 热情玫红渐变）——
    val Brand = Color(0xFFFF6A3D)
    val BrandDeep = Color(0xFFF2542A)
    val BrandViolet = Color(0xFFFF3D81)
    val BrandSoft = Color(0xFFFFEDE6)
    val BrandSofter = Color(0xFFFFF6F2)

    // —— 辅助色 ——
    val Mint = Color(0xFF12B886)
    val MintSoft = Color(0xFFE6F8F1)
    val Amber = Color(0xFFFFB020)
    val AmberSoft = Color(0xFFFFF4DE)

    // —— 语义色（判分 / 状态）——
    val Success = Color(0xFF1FB573)
    val SuccessSoft = Color(0xFFE7F8F0)
    val Danger = Color(0xFFFF4D6D)
    val DangerSoft = Color(0xFFFFE9EE)

    // —— 中性色 ——
    val Bg = Color(0xFFFFF6F2)
    val Surface = Color(0xFFFFFFFF)
    val FieldBg = Color(0xFFFFF1EC)
    val Divider = Color(0xFFF3E3DC)

    val TextPrimary = Color(0xFF2B1F1A)
    val TextSecondary = Color(0xFF8A6F66)
    val TextMuted = Color(0xFFB79A90)
}

private val TesColorScheme = lightColorScheme(
    primary = TesEduColors.Brand,
    onPrimary = Color.White,
    primaryContainer = TesEduColors.BrandSoft,
    onPrimaryContainer = TesEduColors.BrandDeep,
    secondary = TesEduColors.Mint,
    onSecondary = Color.White,
    secondaryContainer = TesEduColors.MintSoft,
    onSecondaryContainer = Color(0xFF0B6E60),
    tertiary = TesEduColors.Amber,
    onTertiary = Color.White,
    background = TesEduColors.Bg,
    onBackground = TesEduColors.TextPrimary,
    surface = TesEduColors.Surface,
    onSurface = TesEduColors.TextPrimary,
    surfaceVariant = TesEduColors.FieldBg,
    onSurfaceVariant = TesEduColors.TextSecondary,
    outline = TesEduColors.Divider,
    outlineVariant = TesEduColors.Divider,
    error = TesEduColors.Danger,
    onError = Color.White,
    errorContainer = TesEduColors.DangerSoft,
    onErrorContainer = Color(0xFF8C1D2F)
)

/**
 * 中文正文比拉丁字母需要更大的行高与字号：
 * bodyMedium 14sp/22sp、bodySmall 12.5sp/19sp 是中文阅读的舒适区间。
 */
private val TesTypography = Typography(
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 22.sp),
    bodySmall = TextStyle(fontSize = 12.5.sp, lineHeight = 19.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 15.sp, fontWeight = FontWeight.Medium)
)

private val TesShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(22.dp),
    extraLarge = RoundedCornerShape(28.dp)
)

@Composable
fun TesEduTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = TesColorScheme,
        typography = TesTypography,
        shapes = TesShapes,
        content = content
    )
}
