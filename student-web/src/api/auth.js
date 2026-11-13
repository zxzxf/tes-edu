import axios from 'axios'

// 登录走根路径 /login：与 admin 一致，网关该路由 mode 空不鉴权，
// 直通 web-backend 签发 RS256 JWT。不能用 client 的 /api/student 前缀——
// 未带 token 时会被网关挡在链路外，根本到不了签发逻辑。
// 手动解包一层：auth store 消费的是 res.access_token / res.role。
export function login(username, password) {
  return axios.post('/login', { username, password }).then((res) => res.data)
}

// ─────────────────────────────────────────────────────────────
// 微信小程序扫码登录（待实现）
// 后端尚未提供扫码登录 ticket 生成与轮询确认接口，以下为前端脚手架占位。
// 正式接入时替换为实现：
//   1) getWxLoginQr()  → 调后端生成 ticket + 二维码内容（返回 { ticket, qrContent }）
//   2) pollWxLogin(ticket) → 轮询 ticket 状态，用户在小程序确认后返回 { access_token, role }
// 当前 UI 已展示「微信扫码登录」入口与二维码占位，但点击/轮询均不生效。
// ─────────────────────────────────────────────────────────────
export function getWxLoginQr() {
  // TODO(微信扫码登录): 调后端 /wx-login/qr 生成 ticket 与二维码内容
  return Promise.reject(new Error('微信小程序扫码登录待实现'))
}

export function pollWxLogin(ticket) {
  // TODO(微信扫码登录): 轮询 /wx-login/poll?ticket= 直到用户确认返回 token
  void ticket
  return Promise.reject(new Error('微信小程序扫码登录待实现'))
}
