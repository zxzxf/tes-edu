package com.tes.edu.data.local

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
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
    /** 最近错的排前面——复习时先看最新的。 */
    @Query("SELECT * FROM wrong_questions ORDER BY createdAt DESC") fun all(): Flow<List<WrongQuestion>>
    @Query("SELECT COUNT(*) FROM wrong_questions WHERE question = :q")
    suspend fun countByQuestion(q: String): Int
    @Delete suspend fun delete(q: WrongQuestion)
    @Query("DELETE FROM wrong_questions") suspend fun clear()
}

// P0 本地库骨架：背诵卡 + 错题本离线缓存（无网可背可复习）
@Database(entities = [DrillCard::class, WrongQuestion::class], version = 2)
abstract class AppDatabase : RoomDatabase() {
    abstract fun drillCardDao(): DrillCardDao
    abstract fun wrongQuestionDao(): WrongQuestionDao

    companion object {
        @Volatile
        private var instance: AppDatabase? = null

        fun get(ctx: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    ctx.applicationContext,
                    AppDatabase::class.java,
                    "tes_edu.db"
                )
                    .addMigrations(MIGRATION_1_2)
                    .build()
                    .also { instance = it }
            }

        /**
         * v1 → v2：错题本补判分归因字段。
         * 只 ADD COLUMN（不带 NOT NULL、给不了默认值就留空），老数据一条不丢。
         */
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN error_type TEXT")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN first_wrong_step INTEGER")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN comment TEXT")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN correction TEXT")
            }
        }
    }
}
