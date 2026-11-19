package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * 推送中心学生收件箱（web-backend /api/student/push/ 系列端点）。
 *
 * 身份铁律：receiver 不在参数里——服务端只认网关注入的 X-User-Id，
 * App 带 Bearer 即可。路径走 /api/student/push/（网关该前缀路由至 web-backend）；
 * 若漏掉 api/student 前缀会落到 / fallback 打到 agent_backend。
 *
 * 长轮询 poll：服务端挂起上限 25s（< Cloudflare 30s 边缘超时红线）。
 * 共用 ApiClient 的 OkHttp（readTimeout=180s > 25s），客户端无需特调；
 * 游标用**微秒** since_us——毫秒截断会让同条消息下一轮重复命中（后端实测坑）。
 */
interface PushApi {

    /** 收件箱列表：主题 Tab 过滤 + 分页；随包回未读总数与分主题未读（Tab 角标）。 */
    @GET("api/student/push/messages")
    suspend fun messages(
        @Query("topic_code") topicCode: String? = null,
        @Query("unread_only") unreadOnly: Boolean = false,
        @Query("page") page: Int = 1,
        @Query("size") size: Int = 30
    ): PushMessagesResponse

    /** 长轮询：有新消息立即返回，否则挂起 ≤25s 空手而归；调用方循环续拉。 */
    @GET("api/student/push/messages/poll")
    suspend fun poll(@Query("since_us") sinceUs: Long): PushPollResponse

    /** 标已读（仅本人消息；别人的 404）。 */
    @POST("api/student/push/messages/{msgId}/read")
    suspend fun markRead(@Path("msgId") msgId: String): OkResponse

    /** 全部已读；传 topicCode 则只清该主题。返回标记条数。 */
    @POST("api/student/push/messages/read-all")
    suspend fun readAll(@Query("topic_code") topicCode: String? = null): ReadAllResponse
}

data class PushMessageItem(
    @SerializedName("msg_id") val msgId: String = "",
    @SerializedName("topic_code") val topicCode: String = "",
    val title: String = "",
    val body: String = "",
    val payload: Map<String, Any?>? = null,
    val read: Boolean = false,
    /** epoch 毫秒（列表口径）；poll 响应里是 created_at_us 微秒。 */
    @SerializedName("created_at_ms") val createdAtMs: Long = 0
)

data class PushMessagesResponse(
    val total: Int = 0,
    val page: Int = 1,
    val size: Int = 30,
    @SerializedName("unread_total") val unreadTotal: Int = 0,
    /** 分主题未读数（Tab 角标数据源）。 */
    @SerializedName("unread_by_topic") val unreadByTopic: Map<String, Int> = emptyMap(),
    val items: List<PushMessageItem> = emptyList()
)

data class PushPollItem(
    @SerializedName("msg_id") val msgId: String = "",
    @SerializedName("topic_code") val topicCode: String = "",
    val title: String = "",
    val body: String = "",
    val payload: Map<String, Any?>? = null,
    val read: Boolean = false,
    @SerializedName("created_at_us") val createdAtUs: Long = 0
)

data class PushPollResponse(
    val items: List<PushPollItem> = emptyList(),
    @SerializedName("max_created_us") val maxCreatedUs: Long = 0
)

data class ReadAllResponse(val marked: Int = 0)
