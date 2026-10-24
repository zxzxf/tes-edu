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
    val role: String? = null
)
