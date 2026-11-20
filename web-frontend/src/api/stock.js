import axios from 'axios'

// 股票盯盘业务中心 API（后端 app/api/stock.py，网关 /api/stock/ rbac+admin）。
// 独立 axios 实例：api/client.js 的 baseURL 固定 /api/admin，域不同不复用；
// 拦截器同款惯例——Bearer 注入、401 清凭证跳登录、响应解包一层。

const stockClient = axios.create({ baseURL: '/api/stock', timeout: 15000 })

stockClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

let _redirecting = false
function redirectToLogin() {
  if (_redirecting) return
  _redirecting = true
  for (const k of ['token', 'user', 'menus', 'perms']) localStorage.removeItem(k)
  const base = (import.meta.env && import.meta.env.BASE_URL) || '/admin/'
  const back = window.location.pathname + window.location.search
  window.location.replace(`${base}login?expired=1&redirect=${encodeURIComponent(back)}`)
}

stockClient.interceptors.response.use(
  (res) => res.data,
  (err) => {
    if (err?.response?.status === 401) redirectToLogin()
    return Promise.reject(err)
  }
)

export default stockClient

// ---------- 行情 ----------
export const getQuotes = (params = {}) => stockClient.get('/quotes', { params })
export const getKline = (symbol, period = 'day', limit = 120) =>
  stockClient.get('/kline', { params: { symbol, period, limit } })
export const getIntraday = (symbol) => stockClient.get('/intraday', { params: { symbol } })
export const searchStock = (q) => stockClient.get('/search', { params: { q } })

// ---------- 自选股 ----------
export const listWatchlist = () => stockClient.get('/watchlist')
export const addWatch = (data) => stockClient.post('/watchlist', data)
export const patchWatch = (id, data) => stockClient.patch(`/watchlist/${id}`, data)
export const delWatch = (id) => stockClient.delete(`/watchlist/${id}`)
export const addGroup = (name) => stockClient.post('/watch-groups', { name })
export const delGroup = (id) => stockClient.delete(`/watch-groups/${id}`)

// ---------- 预警 ----------
export const listAlerts = () => stockClient.get('/alerts')
export const addAlert = (data) => stockClient.post('/alerts', data)
export const patchAlert = (id, data) => stockClient.patch(`/alerts/${id}`, data)
export const delAlert = (id) => stockClient.delete(`/alerts/${id}`)
export const listAlertLogs = (params = {}) => stockClient.get('/alert-logs', { params })

// ---------- 好友 ----------
export const listFriends = () => stockClient.get('/friends')
export const addFriend = (data) => stockClient.post('/friends', data)
export const delFriend = (id) => stockClient.delete(`/friends/${id}`)

// ---------- 推送默认与测试 ----------
export const getPushDefault = () => stockClient.get('/push-default')
export const setPushDefault = (data) => stockClient.put('/push-default', data)
export const pushTest = (receiver) => stockClient.post('/push-test', { receiver })
