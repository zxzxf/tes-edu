package com.tes.edu.data.local.entities

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * 错题本（离线缓存，对应方案 wrong_questions 表）。
 *
 * 注意：既有四个字段**不要加 @ColumnInfo**——Room 默认以字段名建列
 * （`myAnswer` / `createdAt`），改名要靠 ALTER TABLE RENAME COLUMN，
 * Android 自带 SQLite 版本不够，只能走破坏性迁移。新增字段才显式指定列名。
 *
 * v3 起的同步语义（本地是唯一数据源，联网才与后端对账）：
 *  - `clientId`：本地生成的 UUID，是跨端对账的主键（服务端按它 upsert）；
 *  - `remoteId`：服务端 id，回填后补传/删除才认得路；
 *  - `updatedAt`：冲突裁决依据（大者胜）；
 *  - `dirty`：本地有改动待上传；`deleted`：软删待上传（同步成功后本地真删）。
 */
@Entity(tableName = "wrong_questions")
data class WrongQuestion(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val question: String,
    val myAnswer: String,
    val correctAnswer: String,
    val knowledgePoint: String,
    val createdAt: Long = System.currentTimeMillis(),
    // T-R 错题本：判分归因一并存档，复习时才知道当初卡在哪一步
    @ColumnInfo(name = "error_type") val errorType: String? = null,
    @ColumnInfo(name = "first_wrong_step") val firstWrongStep: Int? = null,
    @ColumnInfo(name = "comment") val comment: String? = null,
    @ColumnInfo(name = "correction") val correction: String? = null,
    // —— 云同步（MIGRATION_2_3）——
    // DEFAULT 必须与迁移里的 DEFAULT 字面一致：Room 2.6 把列默认值纳入
    // schema 校验，实体不写 defaultValue 而迁移写了 DEFAULT 会直接抛
    // "Migration didn't properly handle"。
    @ColumnInfo(name = "remote_id") val remoteId: Long? = null,
    @ColumnInfo(name = "client_id") val clientId: String? = null,
    @ColumnInfo(name = "updated_at", defaultValue = "0") val updatedAt: Long = 0,
    @ColumnInfo(name = "dirty", defaultValue = "1") val dirty: Boolean = true,
    @ColumnInfo(name = "deleted", defaultValue = "0") val deleted: Boolean = false
)
