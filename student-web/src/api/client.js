import axios from 'axios'

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

client.interceptors.response.use(
  (res) => res.data,
  (err) => Promise.reject(err)
)

export default client
