package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 离线复习队列（drill_reviews）。
 *
 * 为什么单独一张表而不是给 drill_cards 加一列：断网时同一张卡可能被复习
 * 多次（今天背完又临时加练），单列只能记住最后一次评分，中间的记录会丢。
 * 队列按 id 顺序补传，服务端确认一条删一条。
 *
 * 本地 SM-2 已经先按评分改过 dueAt/ease，所以这里只记"打了多少分"——
 * 服务端重算后会把权威排程回吐，覆盖本地即可。
 */
@Entity(tableName = "drill_reviews")
data class DrillReviewLog(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    /** 服务端卡 id；null 表示这张卡还没 push 上去（等 sync 拿到 id 再补传）。 */
    @ColumnInfo(name = "card_remote_id") val cardRemoteId: Long? = null,
    @ColumnInfo(name = "client_id") val clientId: String = "",
    val rating: Int = 0,
    @ColumnInfo(name = "created_at") val createdAt: Long = 0
)
