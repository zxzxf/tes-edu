package com.tes.edu.state

import com.tes.edu.data.remote.GraphNode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * 知识点「编码 -> 中文名」的进程内缓存。
 *
 * 数据来源：知识图谱接口 `GET /learning/knowledge-graph` 的 nodes（每个节点带
 * `code` 与 `title`）。错题本 / 背诵等页面复用这份映射，把 `MA-B2-C3-S5-K01`
 * 这类 DAG 编码显示成可读名称，而不是把内部编码直接暴露给学生。
 *
 * 生命周期：随进程存在；首次成功拉取图谱后灌入，之后访问图谱会增量更新。
 * 离线或未拉取过图谱时缓存为空——调用方务必回退到原始编码（优雅降级，
 * 绝不让页面崩或显示空白）。
 */
object KpNameCache {

    private val _names = MutableStateFlow<Map<String, String>>(emptyMap())
    val names: StateFlow<Map<String, String>> = _names.asStateFlow()

    /** 用图谱节点灌入 / 增量更新映射。title 为空时记原编码，保证 [get] 总有值。 */
    fun put(nodes: List<GraphNode>) {
        if (nodes.isEmpty()) return
        val delta = nodes.mapNotNull { n ->
            val code = n.code
            if (code.isBlank()) null
            else code to (n.title?.takeIf { it.isNotBlank() } ?: code)
        }.toMap()
        if (delta.isNotEmpty()) _names.value = _names.value + delta
    }

    /** 取可读名；缺失 / 空缓存时回退到原始编码。 */
    fun get(code: String): String {
        if (code.isBlank()) return code
        return _names.value[code] ?: code
    }
}
