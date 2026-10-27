package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 背诵卡 / 刻板知识点（离线缓存，对应方案 drill_cards 表）。
 *
 * v3 起承载 SM-2 间隔复习：`ease`/`intervalDays`/`reps`/`lapses`/`dueAt`
 * 本地先算、本地先生效，联网后把评分补传给服务端（服务端是排程的最终权威）。
 *
 * `front`/`back` 是正式的正反面；老数据只有 `title`/`content`，
 * 展示统一走 [frontText] / [backText] 兜底，不做数据回填（没必要写迁移）。
 */
@Entity(tableName = "drill_cards")
data class DrillCard(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    val content: String,
    val reviewAt: Long = 0,
    // —— 间隔复习（MIGRATION_2_3）——
    @ColumnInfo(name = "remote_id") val remoteId: Long? = null,
    @ColumnInfo(name = "client_id") val clientId: String? = null,
    val kp: String? = null,
    val front: String? = null,
    val back: String? = null,
    @ColumnInfo(defaultValue = "2.5") val ease: Double = 2.5,
    @ColumnInfo(name = "interval_days", defaultValue = "0") val intervalDays: Double = 0.0,
    @ColumnInfo(name = "due_at", defaultValue = "0") val dueAt: Long = 0,
    @ColumnInfo(defaultValue = "0") val reps: Int = 0,
    @ColumnInfo(defaultValue = "0") val lapses: Int = 0,
    @ColumnInfo(name = "updated_at", defaultValue = "0") val updatedAt: Long = 0,
    @ColumnInfo(defaultValue = "1") val dirty: Boolean = true,
    @ColumnInfo(defaultValue = "0") val deleted: Boolean = false
) {
    /** 正面：优先 front，老数据回落到 title。 */
    val frontText: String get() = front?.takeIf { it.isNotBlank() } ?: title

    /** 背面：优先 back，老数据回落到 content。 */
    val backText: String get() = back?.takeIf { it.isNotBlank() } ?: content
}
