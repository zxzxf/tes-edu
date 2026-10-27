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
    @ColumnInfo(name = "correction") val correction: String? = null
)
