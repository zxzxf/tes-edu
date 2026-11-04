package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 作业列表缓存（一次一行，`cache_key` 是学生 id）。
 *
 * 为什么单独存列表：`exam_paper_cache` 缓存的是「某份卷的题干」，学生打开
 * 「作业」tab 走的是 `GET /api/student/exams`，那条链路一失败就是整页空白 +
 * 「网络不可用」。有了这一行，弱网/断网进来至少能看见上次的作业清单
 * （标题、题数、是否已交），点进去还有题干缓存兜底。
 *
 * 按学生 id 分行：同一台设备换账号登录时不会看到别人的作业清单。
 * 缓存内容是**下发清单**（标题这类元数据），不含答案，落明文库与既有
 * 错题本/背诵卡一致，不额外加密。
 */
@Entity(tableName = "exam_list_cache")
data class ExamListCache(
    /** 学生 id（`StudentSession.currentId()`）；未登录时用 "anonymous"。 */
    @PrimaryKey
    @ColumnInfo(name = "cache_key") val cacheKey: String,
    /** `List<StudentExamItem>` 的 Gson JSON。 */
    @ColumnInfo(name = "items_json") val itemsJson: String,
    @ColumnInfo(name = "updated_at") val updatedAt: Long
)
