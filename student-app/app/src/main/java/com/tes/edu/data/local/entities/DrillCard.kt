package com.tes.edu.data.local.entities

import androidx.room.Entity
import androidx.room.PrimaryKey

// 背诵卡 / 刻板知识点（离线缓存，对应方案 drill_cards 表）
@Entity(tableName = "drill_cards")
data class DrillCard(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val title: String,
    val content: String,
    val reviewAt: Long = 0
)
