package com.tes.edu.util

import android.content.Context
import android.os.Build
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 调试用全局崩溃收集器。
 *
 * - 接管 [Thread.setDefaultUncaughtExceptionHandler]，捕获任何未处理异常
 *   （含主线程与协程冒泡到默认处理器的崩溃）。
 * - 把完整堆栈 + 设备信息写入 App 私有外部存储（[getExternalFilesDir]，无需权限，
 *   不受作用域存储限制），持久化跨启动。
 * - 下次启动 [consumePendingCrash] 返回上次崩溃文本，便于无 adb 也能把堆栈发给开发。
 * - 捕获后仍委托原始处理器，系统"应用停止运行"对话框照常出现。
 */
object CrashReporter {

    private const val DIR = "crash_logs"
    private const val PENDING = "pending_crash.txt"
    private const val ALL = "crash_all.txt"
    private const val MAX_ALL_BYTES = 200 * 1024

    private var originalHandler: Thread.UncaughtExceptionHandler? = null
    private lateinit var appContext: Context

    fun init(context: Context) {
        if (::appContext.isInitialized) return
        appContext = context.applicationContext
        originalHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            val report = buildReport(thread, throwable)
            writeReport(report)
            Log.e("CrashReporter", report)
            originalHandler?.uncaughtException(thread, throwable)
        }
    }

    private fun buildReport(thread: Thread, throwable: Throwable): String {
        val sw = StringWriter()
        throwable.printStackTrace(PrintWriter(sw))
        val stack = sw.toString()
        val ts = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        val device =
            "${Build.MANUFACTURER} ${Build.MODEL} / SDK ${Build.VERSION.SDK_INT} / Android ${Build.VERSION.RELEASE}"
        return buildString {
            appendLine("=== Crash @ $ts ===")
            appendLine("Thread: ${thread.name} (#${thread.id})")
            appendLine("Device: $device")
            appendLine("App: ${appContext.packageName}")
            appendLine("--- Stack ---")
            appendLine(stack)
            appendLine("================")
        }
    }

    private fun crashDir(): File {
        val dir = File(appContext.getExternalFilesDir(null), DIR)
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    private fun writeReport(report: String) {
        try {
            val pending = File(crashDir(), PENDING)
            pending.writeText(report)
            val all = File(crashDir(), ALL)
            all.appendText("\n$report")
            if (all.length() > MAX_ALL_BYTES) {
                val text = all.readText()
                all.writeText(text.takeLast(MAX_ALL_BYTES / 2))
            }
        } catch (e: Exception) {
            Log.e("CrashReporter", "write failed", e)
        }
    }

    /** 下次启动调用：有未读崩溃则返回文本并清除 pending（无则返回 null）。 */
    fun consumePendingCrash(): String? = try {
        val pending = File(crashDir(), PENDING)
        if (pending.exists()) {
            val text = pending.readText().also { pending.delete() }
            text
        } else null
    } catch (e: Exception) {
        null
    }
}
