import client from './client'

// 通用 App 推送中心 · 主站 admin 管理面（/api/admin/push/*，client baseURL=/api/admin）。
// 鉴权口径：后端 require_admin 只认网关注入的 X-User-Role=admin（主站 JWT），
// 非 admin 一律 403——菜单授权与此对齐（仅 admin，见 rbac_seed）。

// ---- 全局统计（只见数量不见正文，v1.4 隐私口径）----
export function getPushStats(days = 7) {
  return client.get('/push/stats', { params: { days } })
}

// ---- 第三方账号审批 ----
export function listPushAccounts(status) {
  return client.get('/push/accounts', { params: status ? { status } : {} })
}
export function approvePushAccount(accountId, action, reason) {
  return client.post(`/push/accounts/${accountId}/approve`, { action, reason: reason || null })
}
export function grantPushRole(accountId, role, scopeAppId) {
  return client.post(`/push/accounts/${accountId}/roles`, {
    role, scope_app_id: scopeAppId || null,
  })
}

// ---- 渠道管理 ----
export function listPushChannels() {
  return client.get('/push/channels')
}
// credentials 仅提交时加密落库，接口永不回显（has_credentials 只给布尔）
export function upsertPushChannel(channelType, data) {
  return client.put(`/push/channels/${channelType}`, data)
}
export function testPushChannel(channelType, receiver) {
  return client.post(`/push/channels/${channelType}/test`, {
    channel_type: channelType, receiver,
  })
}

// ---- 平台自有应用 / 主题 / token（admin 代管）----
export function listAllApps() {
  return client.get('/push/apps')
}
export function listAppTopics(appId) {
  return client.get(`/push/apps/${appId}/topics`)
}
export function togglePushTopic(topicId, enabled) {
  return client.patch(`/push/topics/${topicId}`, { enabled })
}
export function listAppTokens(appId) {
  return client.get(`/push/apps/${appId}/tokens`)
}
export function revokePlatformToken(tokenId) {
  return client.post(`/push/tokens/${tokenId}/revoke`)
}
export function listPushMessages(params) {
  return client.get('/push/messages', { params })
}
export function resendPushMessage(msgId) {
  return client.post(`/push/messages/${msgId}/resend`)
}
export function createPlatformApp(data) {
  return client.post('/push/apps', data)
}
export function createPlatformTopic(appId, data) {
  return client.post(`/push/apps/${appId}/topics`, data)
}
export function issuePlatformToken(appId, tier, rateLimitPerMin) {
  return client.post(`/push/apps/${appId}/tokens`, {
    tier, rate_limit_per_min: rateLimitPerMin || null,
  })
}
