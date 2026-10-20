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

client.interceptors.response.use(
  (res) => res.data,
  (err) => Promise.reject(err)
)

export default client
