import axios from 'axios'
import { useAuthStore } from '../stores/auth'

// 学生端 API 统一走 /api/student 前缀（T-S2 接入后端「我的项目」等接口时启用）。
// 网关将以 jwt_c 模式校验令牌（只验令牌不验角色，C 端用户态），
// 细粒度权限由 web-backend 的 require_perm 控制。
const client = axios.create({
  baseURL: '/api/student',
  timeout: 10000,
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ─────────────────────────────────────────────────────────────
// 登录时效（token 过期/失效）处理
// 任何走 client 的请求收到 401 → 标记 sessionExpired（App.vue 据此弹出登录弹窗），
// 并把本次请求挂起入队；用户重新登录成功后由 flushUnauthorized() 统一重放，
// 让用户「无感续接」当前页面，而不是被踢回登录页。
// ─────────────────────────────────────────────────────────────
let waiters = [] // { resolve, reject, config } 列表，重认证后兑现

function onUnauthorized(config) {
  const auth = useAuthStore()
  // 重放后若仍 401：直接拒绝，避免弹窗/请求死循环
  if (config && config.__replaying) {
    return Promise.reject(new Error('re-auth still unauthorized'))
  }
  // 首次触发才置位（避免并发 401 弹出多个弹窗）
  if (!auth.sessionExpired) {
    auth.sessionExpired = true
  }
  // 挂起本次请求：重认证成功后由 flushUnauthorized 重放并以真实响应兑现
  return new Promise((resolve, reject) => {
    waiters.push({ resolve, reject, config: config || null })
  })
}

// 重认证成功后调用：重放挂起请求，并以重放的真实响应兑现原调用方，
// 实现「无感续接」——用户重新登录后，原来的接口调用照常拿到数据。
export function flushUnauthorized() {
  const token = useAuthStore().token
  const ws = waiters.splice(0)
  ws.forEach(({ resolve, reject, config }) => {
    if (!config) {
      resolve(token)
      return
    }
    config.__reauthQueued = false
    config.__replaying = true
    client
      .request(config)
      .then((res) => resolve(res))
      .catch((err) => reject(err))
  })
}

// 用户放弃重认证（返回登录页）：拒绝所有挂起请求
export function cancelUnauthorized() {
  const ws = waiters.splice(0)
  ws.forEach(({ reject }) => reject(new Error('re-auth cancelled')))
}

client.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status
    if (status === 401) {
      // token 失效/过期：弹出登录，并暂存本次请求待重放
      return onUnauthorized(err.config)
    }
    return Promise.reject(err)
  }
)

export default client
