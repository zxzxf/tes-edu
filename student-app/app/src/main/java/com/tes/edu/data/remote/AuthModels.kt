package com.tes.edu.data.remote

import com.google.gson.annotations.SerializedName

/**
 * 占位登录（web-backend POST /login，P2 接入真实用户库前用于端到端联调）。
 * 登录成功后 token 以 Bearer 形式经网关校验，网关注入 X-User-* 身份头。
 */
data class LoginRequest(
    val username: String,
    val password: String
)

data class TokenResponse(
    @SerializedName("access_token") val accessToken: String,
    @SerializedName("token_type") val tokenType: String? = "bearer",
    val role: String? = null,
    /**
     * 登录账号的流水号（T-WB1）。服务端对 student 角色强制用网关注入的
     * X-User-Id 作 student_id，所以这里拿到的就是同步该用的流水号——
     * 与后端口径一致，避免客户端自造一个对不上的 id。
     * 老服务端不返回该字段时为 null，调用方回落占位学号。
     */
    @SerializedName("user_id") val userId: String? = null,
    @SerializedName("tenant_id") val tenantId: String? = null
)

/**
 * M0-3：学生自查年级响应（GET /api/student/profile/me）。
 *
 * 后端在未配置年级时按租户默认（高三）兜底并标 fallback=true，客户端照存即可。
 * grade 可能为 null（老服务端 / 字段缺失），调用方按「未知年级」处理，不崩。
 */
data class StudentProfileResp(
    @SerializedName("student_id") val studentId: String? = null,
    @SerializedName("tenant_id") val tenantId: String? = null,
    val grade: String? = null,
    val fallback: Boolean = false,
    val grades: List<String> = emptyList()
)
