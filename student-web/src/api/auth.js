import axios from 'axios'

// 登录走根路径 /login：与 admin 一致，网关该路由 mode 空不鉴权，
// 直通 web-backend 签发 RS256 JWT。不能用 client 的 /api/student 前缀——
// 未带 token 时会被网关挡在链路外，根本到不了签发逻辑。
// 手动解包一层：auth store 消费的是 res.access_token / res.role。
export function login(username, password) {
  return axios.post('/login', { username, password }).then((res) => res.data)
}
