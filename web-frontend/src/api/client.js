import axios from 'axios'

// 所有请求走 /api/admin 前缀；开发时由 Vite 代理到 Go 网关(:8080)，
// 网关剥离 /api/admin 后转发到 web-backend。
const client = axios.create({
  baseURL: '/api/admin',
  timeout: 10000
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 登录态失效的统一兜底。
// 网关对「过期 / 无效 / 缺失」的 token 一律 401（响应体 {"error":"invalid token"}），
// token 有效期 24h（后端 JWT_EXPIRE_MINUTES）。
// 此前拦截器只把错误 reject 出去，页面各组件各自拿到 401 却没有任何兜底动作——
// 用户看到的是「模型配置页报错 401」，并不知道要重新登录，也不会自动跳登录页
// （2026-10-29 线上报障：隔天打开后台，/menu/my 与 /byom/* 全部 401）。
// 这里统一清凭证 + 跳登录，并带上原地址，登录后自动回跳。
let _redirecting = false

function redirectToLogin() {
  if (_redirecting) return // 并发多个 401 只跳一次
  _redirecting = true
  for (const k of ['token', 'user', 'menus', 'perms']) localStorage.removeItem(k)
  // createWebHistory('/admin/')：BASE_URL 由 Vite 注入，单测环境回退 '/admin/'
  const base = (import.meta.env && import.meta.env.BASE_URL) || '/admin/'
  const back = window.location.pathname + window.location.search
  const target = `${base}login?expired=1&redirect=${encodeURIComponent(back)}`
  window.location.replace(target)
}

client.interceptors.response.use(
  (res) => res.data,
  (err) => {
    // 只认 401（鉴权失效）。403 是权限不足，应保留在页面上如实提示，不能跳登录。
    if (err?.response?.status === 401) redirectToLogin()
    return Promise.reject(err)
  }
)

export default client
