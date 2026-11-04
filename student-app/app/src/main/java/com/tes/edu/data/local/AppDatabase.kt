package com.tes.edu.data.local

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Upsert
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.tes.edu.data.local.entities.DrillCard
import com.tes.edu.data.local.entities.DrillReviewLog
import com.tes.edu.data.local.entities.ExamDraft
import com.tes.edu.data.local.entities.ExamListCache
import com.tes.edu.data.local.entities.ExamPaperCache
import com.tes.edu.data.local.entities.ExamResultCache
import com.tes.edu.data.local.entities.WrongQuestion
import kotlinx.coroutines.flow.Flow

@Dao
interface DrillCardDao {
    @Insert suspend fun insert(card: DrillCard)

    /** 上传/合并统一走 upsert：本地 id 保持不动，服务端对账才不会错行。 */
    @Upsert suspend fun upsert(card: DrillCard)
    @Upsert suspend fun upsertAll(cards: List<DrillCard>)

    /** 未软删的卡，按到期时间升序——先背最该背的那张。 */
    @Query("SELECT * FROM drill_cards WHERE deleted = 0 ORDER BY due_at ASC") fun all(): Flow<List<DrillCard>>

    /** 一次性读取（同步对账用，含已软删的：软删记录也要上传）。 */
    @Query("SELECT * FROM drill_cards") suspend fun allOnce(): List<DrillCard>

    @Query("SELECT * FROM drill_cards WHERE dirty = 1") suspend fun dirtyOnce(): List<DrillCard>

    /** 到期队列：离线也按这个背。 */
    @Query(
        "SELECT * FROM drill_cards WHERE deleted = 0 AND due_at <= :now " +
            "ORDER BY due_at ASC LIMIT :limit"
    )
    suspend fun dueOnce(now: Long, limit: Int): List<DrillCard>

    @Query("SELECT * FROM drill_cards WHERE remote_id = :rid LIMIT 1")
    suspend fun byRemoteId(rid: Long): DrillCard?

    /** 完成态要告诉学生"下次什么时候再来"，取全库最近的到期时间。 */
    @Query("SELECT MIN(due_at) FROM drill_cards WHERE deleted = 0 AND due_at > :now")
    suspend fun nextDueAt(now: Long): Long?

    @Query("DELETE FROM drill_cards WHERE id IN (:ids)") suspend fun deleteByIds(ids: List<Long>)
    @Query("DELETE FROM drill_cards") suspend fun clear()
}

/** 离线复习补传队列。 */
@Dao
interface DrillReviewDao {
    @Insert suspend fun insert(log: DrillReviewLog)
    @Query("SELECT * FROM drill_reviews ORDER BY id ASC") suspend fun all(): List<DrillReviewLog>
    @Query("DELETE FROM drill_reviews WHERE id = :id") suspend fun delete(id: Long)
    @Query("DELETE FROM drill_reviews") suspend fun clear()
}

@Dao
interface WrongQuestionDao {
    @Insert suspend fun insert(q: WrongQuestion)

    /** 服务端回吐 / 本地软删都走 upsert（带 id 覆盖，不带 id 新增）。 */
    @Upsert suspend fun upsert(q: WrongQuestion)
    @Upsert suspend fun upsertAll(items: List<WrongQuestion>)

    /** 最近错的排前面——复习时先看最新的。软删的不进列表（等同步真删）。 */
    @Query("SELECT * FROM wrong_questions WHERE deleted = 0 ORDER BY createdAt DESC")
    fun all(): Flow<List<WrongQuestion>>

    /** 一次性读取：同步对账要看全量（含待上传的软删记录）。 */
    @Query("SELECT * FROM wrong_questions ORDER BY createdAt DESC")
    suspend fun allOnce(): List<WrongQuestion>

    /** 待上传：dirty 即"本地有改动"。 */
    @Query("SELECT * FROM wrong_questions WHERE dirty = 1")
    suspend fun dirtyOnce(): List<WrongQuestion>

    /** 未同步条数（UI 顶部提示用）。 */
    @Query("SELECT COUNT(*) FROM wrong_questions WHERE dirty = 1") fun dirtyCount(): Flow<Int>

    @Query("SELECT COUNT(*) FROM wrong_questions WHERE question = :q AND deleted = 0")
    suspend fun countByQuestion(q: String): Int

    @Query("DELETE FROM wrong_questions WHERE id IN (:ids)") suspend fun deleteByIds(ids: List<Long>)
    @Query("DELETE FROM wrong_questions") suspend fun clear()
}

/** 作业卷作答草稿：一份卷一行，边答边落盘，提交成功后删。 */
@Dao
interface ExamDraftDao {
    @Upsert suspend fun upsert(draft: ExamDraft)

    @Query("SELECT * FROM exam_drafts WHERE assignment_id = :assignmentId LIMIT 1")
    suspend fun get(assignmentId: Long): ExamDraft?

    @Query("DELETE FROM exam_drafts WHERE assignment_id = :assignmentId")
    suspend fun delete(assignmentId: Long)

    @Query("SELECT COUNT(*) FROM exam_drafts") suspend fun count(): Int
}

/** 作业卷题干缓存：一份卷一行，按 assignmentId 命中即秒开（见 [com.tes.edu.viewmodel.ExamViewModel]）。 */
@Dao
interface ExamPaperCacheDao {
    @Query("SELECT * FROM exam_paper_cache WHERE assignment_id = :assignmentId LIMIT 1")
    suspend fun get(assignmentId: Long): ExamPaperCache?

    @Upsert suspend fun upsert(cache: ExamPaperCache)

    @Query("DELETE FROM exam_paper_cache WHERE assignment_id = :assignmentId")
    suspend fun delete(assignmentId: Long)
}

/** 作业列表缓存：一个学生一行，弱网进来先出上次的清单（见 [com.tes.edu.viewmodel.ExamViewModel]）。 */
@Dao
interface ExamListCacheDao {
    @Query("SELECT * FROM exam_list_cache WHERE cache_key = :key LIMIT 1")
    suspend fun get(key: String): ExamListCache?

    @Upsert suspend fun upsert(cache: ExamListCache)

    @Query("DELETE FROM exam_list_cache WHERE cache_key = :key")
    suspend fun delete(key: String)
}

/** 判分结果缓存：一个学生 × 一份下发一行，结果页弱网秒开（见 [com.tes.edu.viewmodel.ExamViewModel]）。 */
@Dao
interface ExamResultCacheDao {
    @Query("SELECT * FROM exam_result_cache WHERE cache_key = :key LIMIT 1")
    suspend fun get(key: String): ExamResultCache?

    @Upsert suspend fun upsert(cache: ExamResultCache)

    @Query("DELETE FROM exam_result_cache WHERE cache_key = :key")
    suspend fun delete(key: String)
}

// 本地库：背诵卡 + 错题本 + 复习队列 + 作业草稿 + 卷题干缓存 + 列表缓存 + 结果缓存
@Database(
    entities = [
        DrillCard::class, WrongQuestion::class, DrillReviewLog::class,
        ExamDraft::class, ExamPaperCache::class, ExamListCache::class,
        ExamResultCache::class
    ],
    version = 9
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun drillCardDao(): DrillCardDao
    abstract fun drillReviewDao(): DrillReviewDao
    abstract fun wrongQuestionDao(): WrongQuestionDao
    abstract fun examDraftDao(): ExamDraftDao
    abstract fun paperCacheDao(): ExamPaperCacheDao
    abstract fun examListCacheDao(): ExamListCacheDao

    /** 判分结果缓存 DAO（结果页弱网秒开）。 */
    abstract fun examResultCacheDao(): ExamResultCacheDao

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
                    .addMigrations(
                        MIGRATION_1_2, MIGRATION_2_3, MIGRATION_3_4, MIGRATION_4_5,
                        MIGRATION_5_6, MIGRATION_6_7, MIGRATION_7_8, MIGRATION_8_9
                    )
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

        /**
         * v2 → v3：错题本云同步 + 背诵卡间隔复习。
         *
         * 依然是纯增量：ADD COLUMN / CREATE TABLE，老数据一条不丢。
         * ⚠️ 每列的类型与 DEFAULT 必须与实体上 `@ColumnInfo(defaultValue = ...)`
         * 逐字一致——Room 2.6 会把列默认值也纳入迁移后的 schema 校验，
         * 一边有 DEFAULT 一边没有会直接抛 "Migration didn't properly handle"。
         */
        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN remote_id INTEGER")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN client_id TEXT")
                db.execSQL(
                    "ALTER TABLE wrong_questions ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"
                )
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")

                db.execSQL("ALTER TABLE drill_cards ADD COLUMN remote_id INTEGER")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN client_id TEXT")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN kp TEXT")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN front TEXT")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN back TEXT")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN ease REAL NOT NULL DEFAULT 2.5")
                db.execSQL(
                    "ALTER TABLE drill_cards ADD COLUMN interval_days REAL NOT NULL DEFAULT 0"
                )
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN due_at INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN reps INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN lapses INTEGER NOT NULL DEFAULT 0")
                db.execSQL(
                    "ALTER TABLE drill_cards ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"
                )
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1")
                db.execSQL("ALTER TABLE drill_cards ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")

                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `drill_reviews` (" +
                        "`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                        "`card_remote_id` INTEGER, " +
                        "`client_id` TEXT NOT NULL, " +
                        "`rating` INTEGER NOT NULL, " +
                        "`created_at` INTEGER NOT NULL)"
                )
            }
        }

        /**
         * v3 → v4：作业卷作答草稿（断网可答）。
         *
         * 只 CREATE TABLE，老数据一条不动。列类型/非空与 [ExamDraft] 实体逐字对齐，
         * 否则 Room 的迁移后 schema 校验会抛 "Migration didn't properly handle"。
         */
        private val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `exam_drafts` (" +
                        "`assignment_id` INTEGER NOT NULL, " +
                        "`paper_id` INTEGER NOT NULL, " +
                        "`answers` TEXT NOT NULL, " +
                        "`client_id` TEXT NOT NULL, " +
                        "`updated_at` INTEGER NOT NULL, " +
                        "PRIMARY KEY(`assignment_id`))"
                )
            }
        }

        /**
         * v4 → v5：作业卷题干缓存（弱网秒开，按 assignmentId 命中）。
         *
         * 只 CREATE TABLE，老数据一条不动。列类型/非空与 [ExamPaperCache] 实体逐字对齐，
         * 否则 Room 的迁移后 schema 校验会抛 "Migration didn't properly handle"。
         */
        /**
         * v5 → v6：错题来源卷（M3-9 按卷筛选）。
         *
         * 纯 ADD COLUMN（可空、无默认值），老数据一条不丢；手工收进的错题
         * 没有卷，保持 NULL 即可，不参与按卷筛选。
         */
        private val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN paper_id INTEGER")
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN paper_question_id INTEGER")
            }
        }

        private val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `exam_paper_cache` (" +
                        "`assignment_id` INTEGER NOT NULL, " +
                        "`paper_id` INTEGER NOT NULL, " +
                        "`questions_json` TEXT NOT NULL, " +
                        "`updated_at` INTEGER NOT NULL, " +
                        "PRIMARY KEY(`assignment_id`))"
                )
            }
        }

        /**
         * v6 → v7：作业列表缓存（弱网/断网进来能看见上次的作业清单）。
         *
         * 只 CREATE TABLE，老数据一条不动。列类型/非空与 [ExamListCache] 实体逐字对齐，
         * 否则 Room 的迁移后 schema 校验会抛 "Migration didn't properly handle"。
         */
        private val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `exam_list_cache` (" +
                        "`cache_key` TEXT NOT NULL, " +
                        "`items_json` TEXT NOT NULL, " +
                        "`updated_at` INTEGER NOT NULL, " +
                        "PRIMARY KEY(`cache_key`))"
                )
            }
        }

        /**
         * v7 → v8：判分结果缓存（结果页弱网秒开）。
         *
         * 只 CREATE TABLE，老数据一条不动。列类型/非空与 [ExamResultCache] 实体逐字对齐，
         * 否则 Room 的迁移后 schema 校验会抛 "Migration didn't properly handle"。
         */
        private val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS `exam_result_cache` (" +
                        "`cache_key` TEXT NOT NULL, " +
                        "`assignment_id` INTEGER NOT NULL, " +
                        "`result_json` TEXT NOT NULL, " +
                        "`updated_at` INTEGER NOT NULL, " +
                        "PRIMARY KEY(`cache_key`))"
                )
            }
        }

        /**
         * v8 → v9：错题本题型列（按「选择题/填空题/解答题」筛选）。
         * 纯增量 ADD COLUMN，老数据题型留空（由云端回填或手动收题时补），一条不丢。
         */
        private val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE wrong_questions ADD COLUMN question_type TEXT")
            }
        }
    }
}
