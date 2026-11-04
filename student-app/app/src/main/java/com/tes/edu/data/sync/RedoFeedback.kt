package com.tes.edu.data.sync

import com.tes.edu.data.remote.WrongRedoResult

/**
 * M3-9 重做结果的展示文案（纯 Kotlin，便于 JVM 单测）。
 *
 * 后端只回状态与原因：`resolved` 才是「已归档」，`manual_pending` 不能写成
 * 「答错」——学生答的可能是对的，只是机器判不了，必须交给老师看。
 */
fun redoFeedback(res: WrongRedoResult): String = when {
    res.resolved && res.cardPushed ->
        "答对了，这道题已从错题本归档；相关背诵卡已顺延到下次复习。"
    res.resolved ->
        "答对了，这道题已从错题本归档。"
    res.status == "manual_pending" ->
        "这次答案机器无法自动判定${res.reason?.takeIf { it.isNotBlank() }?.let { "：$it" } ?: ""}，已转给老师看。"
    res.status == "unavailable" ->
        "原题已不可用${res.reason?.takeIf { it.isNotBlank() }?.let { "：$it" } ?: ""}，这道题只能先留在错题本里。"
    res.partial ->
        "部分正确，但还没全对；错题留在本里，看看纠正再来一次。"
    res.correct == false ->
        "这次还不全对，错题留在本里；看看纠正再来一次。"
    else ->
        "已提交，但判分结果暂不可用，稍后再试。"
}

/**
 * 多空题输入拆分：只有出现分隔符（`；` / `;` / 换行 / 竖线）才拆成逐空，
 * 单空题保留原串。**不要**按逗号拆——`1,234`、区间 `[1,2]` 会被拆坏。
 */
fun splitRedoBlanks(input: String): List<String> {
    val text = input.trim()
    if (text.isEmpty()) return emptyList()
    if (!text.any { it == '；' || it == ';' || it == '\n' || it == '|' }) return emptyList()
    return text.split('；', ';', '\n', '|')
        .map { it.trim() }
        .filter { it.isNotEmpty() }
}
