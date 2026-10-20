package com.tes.edu.data.local

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.Query
import androidx.room.RoomDatabase
import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.local.entities.WrongQuestion
import kotlinx.coroutines.flow.Flow

@Dao
interface DrillCardDao {
    @Insert suspend fun insert(card: DrillCard)
    @Query("SELECT * FROM drill_cards") fun all(): Flow<List<DrillCard>>
}

@Dao
interface WrongQuestionDao {
    @Insert suspend fun insert(q: WrongQuestion)
    @Query("SELECT * FROM wrong_questions") fun all(): Flow<List<WrongQuestion>>
}

// P0 本地库骨架：背诵卡 + 错题本离线缓存（无网可背可复习）
@Database(entities = [DrillCard::class, WrongQuestion::class], version = 1)
abstract class AppDatabase : RoomDatabase() {
    abstract fun drillCardDao(): DrillCardDao
    abstract fun wrongQuestionDao(): WrongQuestionDao
}
