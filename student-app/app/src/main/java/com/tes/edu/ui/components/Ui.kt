package com.tes.edu.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.EmojiObjects
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tes.edu.ui.theme.TesEduColors
import com.tes.edu.util.MathPreview
import com.tes.edu.util.MathText

// ------------------------------------------------------------------ 卡片 / 容器

/** 统一的白色卡片：20dp 圆角 + 极轻阴影 + 发丝描边。 */
@Composable
fun SoftCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        color = TesEduColors.Surface,
        shadowElevation = 2.dp,
        border = BorderStroke(1.dp, TesEduColors.Divider.copy(alpha = 0.7f))
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

// ------------------------------------------------------------------ 顶部渐变头图

/**
 * 页面头图：主色渐变 + 两枚半透明装饰圆，用来建立视觉重心，
 * 替代原先「一行黑标题贴在最顶上」的观感。
 */
@Composable
fun TesHero(
    title: String,
    subtitle: String,
    badge: String,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(24.dp))
            .background(
                Brush.linearGradient(
                    listOf(TesEduColors.Brand, TesEduColors.BrandDeep, TesEduColors.BrandViolet)
                )
            )
            .drawBehind {
                // 装饰圆：大而淡，制造空间感，不与文字抢注意力
                drawCircle(
                    color = Color.White.copy(alpha = 0.10f),
                    radius = size.minDimension * 0.58f,
                    center = Offset(size.width * 0.94f, size.height * 0.12f)
                )
                drawCircle(
                    color = Color.White.copy(alpha = 0.07f),
                    radius = size.minDimension * 0.40f,
                    center = Offset(size.width * 0.80f, size.height * 1.02f)
                )
            }
            .padding(horizontal = 20.dp, vertical = 22.dp)
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(34.dp)
                        .clip(RoundedCornerShape(11.dp))
                        .background(Color.White.copy(alpha = 0.18f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Rounded.EmojiObjects,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(19.dp)
                    )
                }
                Spacer(Modifier.width(10.dp))
                Box(
                    Modifier
                        .clip(RoundedCornerShape(50))
                        .background(Color.White.copy(alpha = 0.16f))
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Text(
                        badge,
                        color = Color.White,
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            Text(
                title,
                color = Color.White,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(6.dp))
            Text(
                subtitle,
                color = Color.White.copy(alpha = 0.86f),
                style = MaterialTheme.typography.bodyMedium
            )
        }
    }
}

// ------------------------------------------------------------------ 输入框

/**
 * 软输入框：外部标题（图标 + 文案 + 辅助说明）+ 内部无边框填充文本框。
 * 相比默认 OutlinedTextField 的硬黑描边，填充 + 聚焦时才浮出描边的观感更轻盈。
 */
@Composable
fun SoftFieldCard(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    modifier: Modifier = Modifier,
    hint: String? = null,
    placeholder: String? = null,
    minLines: Int = 3,
    leadingIcon: ImageVector? = null,
    accent: Color = TesEduColors.Brand,
    // 公式预览：开启后，输入里出现 `e^x` 这类写法就在下方实时渲染出来。
    // 只影响展示，`onValueChange` 抛出的仍是原始字符串。
    mathPreview: Boolean = false
) {
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val borderColor by animateColorAsState(
        targetValue = if (focused) accent.copy(alpha = 0.55f) else Color.Transparent,
        animationSpec = tween(180),
        label = "fieldBorder"
    )

    Column(modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (leadingIcon != null) {
                Box(
                    Modifier
                        .size(26.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(accent.copy(alpha = 0.12f)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        leadingIcon,
                        contentDescription = null,
                        tint = accent,
                        modifier = Modifier.size(15.dp)
                    )
                }
                Spacer(Modifier.width(8.dp))
            }
            Text(
                label,
                style = MaterialTheme.typography.titleSmall,
                color = TesEduColors.TextPrimary
            )
            if (hint != null) {
                Spacer(Modifier.width(8.dp))
                Text(
                    hint,
                    style = MaterialTheme.typography.labelSmall,
                    color = TesEduColors.TextMuted
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        TextField(
            value = value,
            onValueChange = onValueChange,
            modifier = Modifier
                .fillMaxWidth()
                .border(1.5.dp, borderColor, RoundedCornerShape(16.dp)),
            placeholder = {
                if (placeholder != null) {
                    Text(
                        placeholder,
                        style = MaterialTheme.typography.bodyMedium,
                        color = TesEduColors.TextMuted
                    )
                }
            },
            textStyle = MaterialTheme.typography.bodyMedium,
            minLines = minLines,
            interactionSource = interaction,
            shape = RoundedCornerShape(16.dp),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = TesEduColors.FieldBg,
                unfocusedContainerColor = TesEduColors.FieldBg,
                disabledContainerColor = TesEduColors.FieldBg,
                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
                disabledIndicatorColor = Color.Transparent,
                cursorColor = accent,
                focusedTextColor = TesEduColors.TextPrimary,
                unfocusedTextColor = TesEduColors.TextPrimary,
                focusedPlaceholderColor = TesEduColors.TextMuted,
                unfocusedPlaceholderColor = TesEduColors.TextMuted
            )
        )
        if (mathPreview) {
            Spacer(Modifier.height(8.dp))
            MathPreview(value)
        }
    }
}

// ------------------------------------------------------------------ 按钮

/** 主 CTA：主色→紫的渐变胶囊，带柔光投影。 */
@Composable
fun GradientButton(
    text: String,
    icon: ImageVector?,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    val shape = RoundedCornerShape(16.dp)
    val brush = if (enabled) {
        Brush.linearGradient(listOf(TesEduColors.Brand, TesEduColors.BrandDeep, TesEduColors.BrandViolet))
    } else {
        Brush.linearGradient(listOf(Color(0xFFE7D8D2), Color(0xFFE7D8D2)))
    }
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(54.dp)
            .shadow(
                elevation = if (enabled) 12.dp else 0.dp,
                shape = shape,
                ambientColor = TesEduColors.Brand.copy(alpha = 0.35f),
                spotColor = TesEduColors.Brand.copy(alpha = 0.35f)
            )
            .clip(shape)
            .background(brush)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) {
                Icon(icon, contentDescription = null, tint = Color.White, modifier = Modifier.size(19.dp))
                Spacer(Modifier.width(8.dp))
            }
            Text(
                text,
                color = Color.White,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

/** 次 CTA：白底 + 品牌色细描边，和主按钮形成明确的层级差。 */
@Composable
fun SoftOutlinedButton(
    text: String,
    icon: ImageVector?,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    val shape = RoundedCornerShape(16.dp)
    val contentColor = if (enabled) TesEduColors.Brand else TesEduColors.TextMuted
    val borderColor = if (enabled) {
        TesEduColors.Brand.copy(alpha = 0.32f)
    } else {
        TesEduColors.Divider
    }
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(52.dp)
            .clip(shape)
            .background(TesEduColors.Surface)
            .border(1.5.dp, borderColor, shape)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (icon != null) {
                Icon(icon, contentDescription = null, tint = contentColor, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
            }
            Text(
                text,
                color = contentColor,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold
            )
        }
    }
}

// ------------------------------------------------------------------ 小零件

/** 信息胶囊：题型、Hint 等级、状态标签统一用它。 */
@Composable
fun InfoPill(
    text: String,
    color: Color,
    bg: Color,
    modifier: Modifier = Modifier
) {
    Box(
        modifier
            .clip(RoundedCornerShape(50))
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 4.dp)
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1
        )
    }
}

/** 轻提示条：图标 + 文案，用于错误 / 需要补过程等非阻塞反馈。 */
@Composable
fun SoftNotice(
    text: String,
    icon: ImageVector,
    color: Color,
    bg: Color,
    modifier: Modifier = Modifier
) {
    Row(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(17.dp))
        Spacer(Modifier.width(8.dp))
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = color
        )
    }
}

/** 「标签：值」一行，用于判分结果的字段罗列。 */
@Composable
fun LabelValueRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = TesEduColors.TextMuted,
            modifier = Modifier.width(88.dp)
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary,
            modifier = Modifier.weight(1f)
        )
    }
}

/** 引述块：证据 / 点评 / 纠正等成段文字用浅底色块承托。 */
@Composable
fun SoftQuote(
    title: String,
    text: String,
    accent: Color,
    bg: Color
) {
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(bg)
            .padding(12.dp)
    ) {
        Text(
            title,
            style = MaterialTheme.typography.labelSmall,
            color = accent,
            fontWeight = FontWeight.SemiBold
        )
        Spacer(Modifier.height(4.dp))
        // 证据/点评/纠正里常带 `x^2`、`\frac` 这类写法，按公式渲染
        MathText(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
    }
}

/** 空状态：柔光圆底 + 图标 + 说明，用于尚未填充的能力页。 */
@Composable
fun EmptyState(
    icon: ImageVector,
    title: String,
    subtitle: String,
    accent: Color = TesEduColors.Brand
) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(36.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Box(
            Modifier
                .size(92.dp)
                .clip(CircleShape)
                .background(
                    Brush.radialGradient(
                        listOf(accent.copy(alpha = 0.18f), accent.copy(alpha = 0.03f))
                    )
                ),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, contentDescription = null, tint = accent, modifier = Modifier.size(40.dp))
        }
        Spacer(Modifier.height(20.dp))
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = TesEduColors.TextPrimary
        )
        Spacer(Modifier.height(6.dp))
        Text(
            subtitle,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextSecondary,
            textAlign = TextAlign.Center
        )
        Spacer(Modifier.height(14.dp))
        Box(
            Modifier
                .clip(RoundedCornerShape(50))
                .background(TesEduColors.FieldBg)
                .padding(horizontal = 12.dp, vertical = 5.dp)
        ) {
            Text(
                "即将上线",
                style = MaterialTheme.typography.labelSmall,
                color = TesEduColors.TextMuted,
                fontSize = 11.sp
            )
        }
    }
}
