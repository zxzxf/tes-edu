import axios from 'axios'

// 学生端 / 过程采集请求走 /api/app 前缀（Vite 与 Nginx 均代理到 Go 网关 :8080，
// 网关按 jwt_c 模式转发到 agent-backend）。
// 注意：网关 jwt_c 只验签名不校验角色，教师/管理员 token 也能调通；
// 但此时学情事件会记在该账号名下——试点机房练习请用学生账号登录。
const agent = axios.create({
  baseURL: '/api/app',
  timeout: 60000 // 解答题走 LLM 判分，推理模型可能耗时 30s+
})

agent.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

agent.interceptors.response.use(
  (res) => res.data,
  (err) => Promise.reject(err)
)

// S4 过程采集：提交题干 + 作答过程，返回讲解与 judge v2 步骤级归因
export function solveWithProcess(data) {
  return agent.post('/solve', data)
}

// 拍照识题（既有 /solve_image OCR 链路；VLM 切步骤暂不做）
export function solveImage(file) {
  const form = new FormData()
  form.append('file', file)
  return agent.post('/solve_image', form)
}
