package com.tes.edu.util

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 公式渲染解析层的红线用例。
 *
 * 只测纯字符串部分（`preprocessMath` / `parseMathSegments` / `hasMath`）——
 * 上下标 Span 的视觉效果由人工过目，这里锁的是"文本不丢、不串、不越界"。
 */
class MathTextTest {

    // ---------------- LaTeX → 可读字符 ----------------

    @Test
    fun fraction_is_flattened() {
        assertEquals("1/2", preprocessMath("\\frac{1}{2}"))
        assertEquals("1/2", preprocessMath("\\dfrac{1}{2}"))
        // 分母含加减号必须补括号，否则 1/(2+3) 会读成 1/2+3
        assertEquals("1/(x+1)", preprocessMath("\\frac{1}{x+1}"))
    }

    @Test
    fun sqrt_is_flattened() {
        assertEquals("√2", preprocessMath("\\sqrt{2}"))
        assertEquals("√(x+1)", preprocessMath("\\sqrt{x+1}"))
        assertEquals("√x", preprocessMath("\\sqrt x"))
    }

    @Test
    fun symbols_are_replaced_and_unknown_kept() {
        assertEquals("π × 2 ≤ 3", preprocessMath("\\pi \\times 2 \\le 3"))
        assertEquals("θ", preprocessMath("\\theta"))
        // 未收录的宏原样保留，绝不吞掉内容
        assertEquals("\\whatsup", preprocessMath("\\whatsup"))
    }

    @Test
    fun delimiters_are_stripped_but_content_kept() {
        val d = "${'$'}"
        assertEquals("e^x 与 x^2", preprocessMath("${d}e^x${d} 与 $d${d}x^2$d$d"))
        assertEquals("a+b", preprocessMath("\\(a+b\\)"))
    }

    @Test
    fun plain_chinese_is_untouched() {
        val src = "已知函数 f(x) 的最大值为 1，求 a"
        assertEquals(src, preprocessMath(src))
    }

    // ---------------- 本次修复的回归用例 ----------------

    @Test
    fun latex_escape_braces_are_unescaped() {
        // 集合/绝对值场景的 `\{` `\}` 必须还原成普通花括号
        assertEquals("A={1,2}", preprocessMath("A=\\{1,2\\}"))
        assertEquals("{-1/2,1}", preprocessMath("\\{-1/2,1\\}"))
    }

    @Test
    fun bare_fraction_shorthand_is_flattened() {
        // `\tfrac12` / `\frac12` 省略花括号的简写分数
        assertEquals("-1/2", preprocessMath("-\\tfrac12"))
        assertEquals("1/2", preprocessMath("\\tfrac12"))
        assertEquals("3/4", preprocessMath("\\frac34"))
    }

    @Test
    fun trig_function_names_drop_backslash() {
        // 函数名保留名字、去掉反斜杠，与 `\pi`→π 风格一致
        assertEquals("sin7π/6", preprocessMath("\\sin7π/6"))
        assertEquals("cos", preprocessMath("\\cos"))
        assertEquals("tan", preprocessMath("\\tan"))
    }

    @Test
    fun double_backslash_is_normalized() {
        // 数据源双重转义：`\\dfrac` 应等价于 `\dfrac`，否则整段露源码
        assertEquals("√65/2", preprocessMath("${'$'}\\\\dfrac{\\\\sqrt{65}}{2}${'$'}"))
        assertEquals("D. √65/2", preprocessMath("D. ${'$'}\\\\dfrac{\\\\sqrt{65}}{2}${'$'}"))
    }

    @Test
    fun escaped_braces_are_unescaped_before_frac() {
        // 题库标准 LaTeX 常把花括号写成转义形式 `\{` `\}`（`\dfrac\{\sqrt\{65\}\}\{2\}`），
        // 还原必须发生在 FRAC/SQRT 之前，否则 `\dfrac` 整段露源码（选项 D 现网故障）。
        assertEquals("√65/2", preprocessMath("\\dfrac{\\sqrt{65}}{2}"))
        assertEquals("·√65/2", preprocessMath("·\\dfrac{\\sqrt{65}}{2}"))
        assertEquals("D. ·√65/2", preprocessMath("D. ·\\dfrac{\\sqrt{65}}{2}"))
        // 简写分数 + 转义花括号也要能展平
        assertEquals("1/2", preprocessMath("\\frac{1}{2}"))
        assertEquals("{-1/2,1}", preprocessMath("\\{-1/2,1\\}"))
    }

    @Test
    fun double_backslash_with_escaped_braces() {
        // 双重转义 + 转义花括号同时出现：`\\dfrac\{\\sqrt\{65\}\}\{2\}`
        assertEquals("√65/2", preprocessMath("\\\\dfrac{\\sqrt{65}}{2}"))
        assertEquals("·√65/2", preprocessMath("·\\\\dfrac{\\sqrt{65}}{2}"))
    }

    @Test
    fun stray_backslash_is_removed() {
        // 残留的孤立反斜杠（如 LaTeX 的 `\ ` 空格命令）必须清掉，绝不露源码
        assertEquals("C_1:y^2=2p_1x (p_1>0)", preprocessMath("${'$'}C_1:y^2=2p_1x\\ (p_1>0)${'$'}"))
        assertEquals("a/b", preprocessMath("\\frac{a}{b}"))
    }

    // ---------------- 上下标切分 ----------------

    private fun segs(src: String) = parseMathSegments(src)

    @Test
    fun superscript_single_char() {
        val s = segs("e^x")
        assertEquals(listOf(MathSegment("e", MathScript.NORMAL), MathSegment("x", MathScript.SUP)), s)
    }

    @Test
    fun superscript_braced_group() {
        val s = segs("x^{n+1}")
        assertEquals(listOf(MathSegment("x", MathScript.NORMAL), MathSegment("n+1", MathScript.SUP)), s)
    }

    @Test
    fun subscript_single_and_braced() {
        assertEquals(
            listOf(MathSegment("a", MathScript.NORMAL), MathSegment("1", MathScript.SUB)),
            segs("a_1")
        )
        assertEquals(
            listOf(MathSegment("a", MathScript.NORMAL), MathSegment("n+1", MathScript.SUB)),
            segs("a_{n+1}")
        )
    }

    @Test
    fun sub_then_sup_keeps_order() {
        assertEquals(
            listOf(
                MathSegment("x", MathScript.NORMAL),
                MathSegment("1", MathScript.SUB),
                MathSegment("2", MathScript.SUP)
            ),
            segs("x_1^2")
        )
    }

    @Test
    fun dangling_script_marks_are_dropped() {
        // 行尾孤立 ^ 不该留下一个光秃秃的符号
        assertEquals(listOf(MathSegment("2", MathScript.NORMAL)), segs("2^"))
    }

    @Test
    fun unclosed_brace_takes_the_rest() {
        assertEquals(
            listOf(MathSegment("x", MathScript.NORMAL), MathSegment("n+1", MathScript.SUP)),
            segs("x^{n+1")
        )
    }

    @Test
    fun roundtrip_only_drops_script_marks() {
        val src = "求 f(x)=x^{2}+e^x 的最小值，其中 a_1=1"
        val joined = segs(src).joinToString("") { it.text }
        assertEquals("求 f(x)=x2+ex 的最小值，其中 a1=1", joined)
        // 只丢掉 `^ { } _` 这 5 个标记字符，其余一字不少（防止解析吞正文）
        assertEquals(src.length - 5, joined.length)
    }

    // ---------------- 是否需要渲染 ----------------

    @Test
    fun hasMath_detects_and_ignores_plain_text() {
        assertTrue(hasMath("求 f(x)=x^2 的导数"))
        assertTrue(hasMath("\\frac{1}{2}"))
        assertTrue(hasMath("${'$'}e^x${'$'}"))
        assertTrue(hasMath("a_{n+1}"))
        assertFalse(hasMath("这是一道语文阅读理解题"))
        assertFalse(hasMath("已知函数最大值为 1"))
    }
}
