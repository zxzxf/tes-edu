package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 判分结果缓存（一次一行，键 = 学生 id + 下发 id）。
 *
 * 为什么单独存结果：结果页原本每次都走 `GET .../result`，跨境隧道一个请求就是
 * 1.5–2.9s；而交卷后结果基本不变（只有老师人工改判/标答复核会改），落盘后
 * 点进去先秒开，再后台静默刷新。
 *
 * 按学生分行：同一台设备换账号登录不会看到别人的成绩。结果是学生自己的成绩，
 * 明文库与既有错题本/背诵卡一致，不额外加密。
 */
@Entity(tableName = "exam_result_cache")
data class ExamResultCache(
    /** `"$studentId:$assignmentId"`；未登录用 anonymous 前缀，宁可不复用也不串号。 */
    @PrimaryKey
    @ColumnInfo(name = "cache_key") val cacheKey: String,
    @ColumnInfo(name = "assignment_id") val assignmentId: Long,
    /** `ExamResultDto` 的 Gson JSON。 */
    @ColumnInfo(name = "result_json") val resultJson: String,
    @ColumnInfo(name = "updated_at") val updatedAt: Long
)
