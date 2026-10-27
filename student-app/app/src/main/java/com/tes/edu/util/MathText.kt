package com.tes.edu.util

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tes.edu.ui.theme.TesEduColors

/**
 * 学生端公式渲染（零依赖，与 web 端 `utils/math.js` 同一口径）。
 *
 * 后端与模型给的都是线性文本（`e^x`、`x^2`、`x^{n+1}`、`\frac{1}{2}`），
 * 直接丢给 `Text` 会原样显示成 "e^x"，可读性很差。这里做两件事：
 * 1. `preprocessMath`：把 LaTeX 片段转成可读字符（`\pi`→π、`\frac{1}{2}`→1/2）；
 * 2. `parseMathSegments` + `buildMathAnnotatedString`：把 `^` / `_` 后面的一段
 *    用 `BaselineShift.Superscript / Subscript` 抬升或下沉，字号缩到 0.72 倍。
 *
 * 契约：**只影响展示层**。提交给后端的仍是最初的原文（含 `^`），
 * 识别结果被学生编辑后回传的也是编辑框里的原始字符串。
 */

/** 片段类型：正文 / 上标 / 下标。 */
enum class MathScript { NORMAL, SUP, SUB }

/** 一段同类型文本。上标段内部可能仍有 `^`（如 `e^{x^2}`），渲染时递归处理。 */
data class MathSegment(val text: String, val script: MathScript)

/** 常见 LaTeX 宏 → Unicode 字符。未收录的宏原样保留，不会把文本吞掉。 */
private val SYMBOLS: Map<String, String> = mapOf(
    "pi" to "π", "alpha" to "α", "beta" to "β", "gamma" to "γ", "delta" to "δ",
    "Delta" to "Δ", "epsilon" to "ε", "varepsilon" to "ε", "zeta" to "ζ",
    "eta" to "η", "theta" to "θ", "vartheta" to "θ", "iota" to "ι",
    "kappa" to "κ", "lambda" to "λ", "mu" to "μ", "nu" to "ν",
    "xi" to "ξ", "rho" to "ρ", "sigma" to "σ", "tau" to "τ",
    "upsilon" to "υ", "phi" to "φ", "varphi" to "φ", "chi" to "χ",
    "psi" to "ψ", "omega" to "ω", "Omega" to "Ω",
    "times" to "×", "div" to "÷", "cdot" to "·", "ast" to "∗",
    "pm" to "±", "mp" to "∓", "le" to "≤", "leq" to "≤",
    "ge" to "≥", "geq" to "≥", "ne" to "≠", "neq" to "≠",
    "approx" to "≈", "equiv" to "≡", "sim" to "∼", "propto" to "∝",
    "infty" to "∞", "partial" to "∂", "nabla" to "∇",
    "sum" to "∑", "prod" to "∏", "int" to "∫", "oint" to "∮",
    "rightarrow" to "→", "to" to "→", "leftarrow" to "←",
    "Rightarrow" to "⇒", "Leftarrow" to "⇐", "leftrightarrow" to "↔",
    "in" to "∈", "notin" to "∉", "subset" to "⊂", "supset" to "⊃",
    "cup" to "∪", "cap" to "∩", "emptyset" to "∅",
    "perp" to "⊥", "parallel" to "∥", "angle" to "∠",
    "triangle" to "△", "square" to "□", "circ" to "°",
    "deg" to "°", "degree" to "°", "prime" to "′",
    "ldots" to "…", "cdots" to "⋯", "dots" to "…"
)

private val FRAC = Regex("""\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}""")
private val SQRT_BRACE = Regex("""\\sqrt\s*\{([^{}]*)\}""")
private val SQRT_BARE = Regex("""\\sqrt\s*([A-Za-z0-9])""")
private val TEXT_MACRO = Regex("""\\(?:text|mathrm|mathbf|operatorname|mathbb)\s*\{([^{}]*)\}""")
private val SPACER = Regex("""\\(?:,|;|!|:|quad|qquad|qquad)""")
private val LEFT_RIGHT = Regex("""\\(?:left|right)\b""")
private val MACRO = Regex("""\\([A-Za-z]+)""")
private val HAS_SUBSCRIPT = Regex("""_[A-Za-z0-9{(]""")

/** 分子/分母含加减号时补括号，避免 `1/(2+3)` 变成 `1/2+3`。 */
private fun wrapOperand(s: String): String {
    val v = s.trim()
    if (v.isEmpty()) return v
    val need = v.length > 1 && v.any { it == '+' || it == '-' || it == '*' || it == '/' }
    return if (need) "($v)" else v
}

/** LaTeX → 可读文本（不做上下标，那是 `parseMathSegments` 的活）。 */
fun preprocessMath(src: String): String {
    var s = src
    s = TEXT_MACRO.replace(s) { it.groupValues[1] }
    s = LEFT_RIGHT.replace(s, "")
    s = FRAC.replace(s) { wrapOperand(it.groupValues[1]) + "/" + wrapOperand(it.groupValues[2]) }
    s = SQRT_BRACE.replace(s) { "√" + wrapOperand(it.groupValues[1]) }
    s = SQRT_BARE.replace(s) { "√" + it.groupValues[1] }
    s = SPACER.replace(s, " ")
    s = MACRO.replace(s) { SYMBOLS[it.groupValues[1]] ?: it.value }
    // 定界符：`$$..$$` / `$..$` / `\(..\)` 只去掉包裹符号，内容保留
    s = s.replace("$$", "").replace("$", "")
    s = s.replace("\\(", "").replace("\\)", "")
    s = s.replace("{}", "")
    return s
}

/**
 * 读取 `^` / `_` 后面的指数体：`{...}` 取到配对右括号，否则只取一个字符
 * （含 Unicode 代理对，避免把 emoji / 生僻字截成乱码）。
 */
private fun readScriptBody(s: String, start: Int): Pair<String, Int> {
    if (start >= s.length) return "" to start
    if (s[start] == '{') {
        val end = s.indexOf('}', start)
        return if (end < 0) s.substring(start + 1) to s.length
        else s.substring(start + 1, end) to end + 1
    }
    val ch = s[start]
    return if (ch.isHighSurrogate() && start + 1 < s.length) s.substring(start, start + 2) to start + 2
    else ch.toString() to start + 1
}

/** 把文本切成「正文 / 上标 / 下标」片段序列。 */
fun parseMathSegments(src: String): List<MathSegment> {
    val out = mutableListOf<MathSegment>()
    val buf = StringBuilder()
    fun flush() {
        if (buf.isNotEmpty()) {
            out.add(MathSegment(buf.toString(), MathScript.NORMAL))
            buf.clear()
        }
    }
    var i = 0
    while (i < src.length) {
        val c = src[i]
        if (c == '^' || c == '_') {
            i++
            val (body, next) = readScriptBody(src, i)
            i = next
            if (body.isNotEmpty()) {
                flush()
                out.add(MathSegment(body, if (c == '^') MathScript.SUP else MathScript.SUB))
            }
            // 空体（如行尾的 `^`）直接丢弃，不留孤立符号
        } else {
            buf.append(c)
            i++
        }
    }
    flush()
    return out
}

/** 是否含公式写法——不含就直接走普通 `Text`，省掉解析开销。 */
fun hasMath(text: String): Boolean =
    text.contains('^') || text.contains('\\') || text.contains('$') ||
        HAS_SUBSCRIPT.containsMatchIn(text)

/** 上/下标字号：按基准字号的 0.72 倍；基准未指定时退回 11sp。 */
private fun scriptSize(style: TextStyle): TextUnit {
    val base = if (style.fontSize != TextUnit.Unspecified) style.fontSize.value else 14f
    return (base * 0.72f).sp
}

/** 递归写入片段；`depth` 限制嵌套层数（`e^{x^2}` 这类）防止异常输入爆栈。 */
private fun AnnotatedString.Builder.appendSegments(
    src: String,
    small: TextUnit,
    depth: Int
) {
    if (depth > 3) {
        append(src)
        return
    }
    for (seg in parseMathSegments(src)) {
        when (seg.script) {
            MathScript.NORMAL -> {
                if (seg.text.contains('^') || seg.text.contains('_')) {
                    appendSegments(seg.text, small, depth + 1)
                } else {
                    append(seg.text)
                }
            }
            MathScript.SUP -> {
                pushStyle(SpanStyle(fontSize = small, baselineShift = BaselineShift.Superscript))
                appendSegments(seg.text, small, depth + 1)
                pop()
            }
            MathScript.SUB -> {
                pushStyle(SpanStyle(fontSize = small, baselineShift = BaselineShift.Subscript))
                appendSegments(seg.text, small, depth + 1)
                pop()
            }
        }
    }
}

/** 线性公式 → 带上下标的 `AnnotatedString`。 */
fun buildMathAnnotatedString(text: String, style: TextStyle): AnnotatedString {
    val src = preprocessMath(text)
    if (!hasMath(src)) return AnnotatedString(src)
    val small = scriptSize(style)
    return buildAnnotatedString { appendSegments(src, small, 0) }
}

/**
 * 会渲染公式的 `Text`：不含公式时与普通 `Text` 完全等价。
 *
 * 用于所有展示 AI 输出的位置（参考答案、分步提示、点评、变式题…）。
 */
@Composable
fun MathText(
    text: String,
    modifier: Modifier = Modifier,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
    color: Color = Color.Unspecified
) {
    val annotated = remember(text, style) {
        if (hasMath(text)) buildMathAnnotatedString(text, style) else AnnotatedString(text)
    }
    Text(
        text = annotated,
        modifier = modifier,
        style = style,
        color = color
    )
}

/**
 * 输入预览：题干/作答框下实时显示渲染结果，只在检测到公式写法时出现。
 * 提交给后端的仍是输入框里的原文——这里纯粹是给学生一个"我打对了没"的确认。
 */
@Composable
fun MathPreview(text: String, modifier: Modifier = Modifier) {
    if (!hasMath(text)) return
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(TesEduColors.BrandSofter)
            .padding(horizontal = 12.dp, vertical = 9.dp)
    ) {
        Text(
            "公式预览",
            style = MaterialTheme.typography.labelSmall,
            color = TesEduColors.Brand
        )
        Spacer(Modifier.height(4.dp))
        MathText(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = TesEduColors.TextPrimary
        )
    }
}
