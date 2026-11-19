import axios from 'axios'

// 第三方推送 portal 专用 client（/api/push/portal/*，Bearer=portal 会话令牌）。
// ⚠️ 与主站 api/client.js 完全分离：主站认 localStorage.token（网关注入头体系），
// portal 认 localStorage.push_portal_session（独立密钥+受众 JWT）。
// 401 处理只清 portal 自己的键、只跳 portal 登录页——绝不触碰主站会话。
const portal = axios.create({
  baseURL: '/api/push/portal',
  timeout: 15000,
})

portal.interceptors.request.use((config) => {
  const t = localStorage.getItem('push_portal_session')
  if (t) config.headers.Authorization = `Bearer ${t}`
  return config
})

portal.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err?.response?.status === 401) {
      localStorage.removeItem('push_portal_session')
      // portal 路由挂在 /admin/ 基路径下（同一 SPA），回 portal 登录页
      const base = (import.meta.env && import.meta.env.BASE_URL) || '/admin/'
      if (!window.location.pathname.endsWith('/push-portal/login')) {
        window.location.replace(`${base}push-portal/login`)
      }
    }
    return Promise.reject(err)
  }
)

// ---- 账号 ----
export function portalRegister(data) { return portal.post('/register', data) }
export function portalLogin(email, password) { return portal.post('/login', { email, password }) }
export function portalMe() { return portal.get('/me') }

// ---- 应用（developer）----
export function portalListApps() { return portal.get('/apps') }
export function portalCreateApp(data) { return portal.post('/apps', data) }
export function portalUpdateApp(appId, data) { return portal.put(`/apps/${appId}`, data) }
export function portalDisableApp(appId) { return portal.delete(`/apps/${appId}`) }

// ---- token（developer 写、三档可读）----
export function portalIssueToken(appId, tier, rateLimitPerMin) {
  return portal.post(`/apps/${appId}/tokens`, { tier, rate_limit_per_min: rateLimitPerMin || null })
}
export function portalListTokens(appId) { return portal.get(`/apps/${appId}/tokens`) }
export function portalRevokeToken(tokenId) { return portal.post(`/tokens/${tokenId}/revoke`) }

// ---- 主题（developer/operator 写、三档可读）----
export function portalListTopics(appId) { return portal.get(`/apps/${appId}/topics`) }
export function portalCreateTopic(appId, data) { return portal.post(`/apps/${appId}/topics`, data) }
export function portalUpdateTopic(topicId, data) { return portal.put(`/topics/${topicId}`, data) }
export function portalDisableTopic(topicId) { return portal.delete(`/topics/${topicId}`) }

// ---- 消息/统计（只读，三档角色梯度）----
export function portalListMessages(appId, params) {
  return portal.get(`/apps/${appId}/messages`, { params })
}
export function portalAppStats(appId, days = 7) {
  return portal.get(`/apps/${appId}/stats`, { params: { days } })
}

// 数据面自查（pth-token 调，非 portal 会话）：接入方排障用
export function openTokenInfo(pthToken) {
  return axios.get('/api/push/open/token-info', {
    headers: { Authorization: `Bearer ${pthToken}` },
    timeout: 10000,
  }).then((r) => r.data)
}
