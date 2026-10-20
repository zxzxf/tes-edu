package com.tes.edu.data.local.entities

import androidx.room.Entity
import androidx.room.PrimaryKey

// 错题本（离线缓存，对应方案 wrong_questions 表）
@Entity(tableName = "wrong_questions")
data class WrongQuestion(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val question: String,
    val myAnswer: String,
    val correctAnswer: String,
    val knowledgePoint: String,
    val createdAt: Long = System.currentTimeMillis()
)
