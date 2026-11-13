import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as apiLogin } from '../api/auth'

// 学生端 auth store（C 端用户态，较 admin 简化：无菜单/角色矩阵，
// 仅维护 token + 用户画像；授权由后端 require_perm 把关）。
// localStorage 缓存供刷新秒开，路由守卫在首屏补校验。

const LS_TOKEN = 'token'
const LS_USER = 'user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(LS_TOKEN) || '')
  const user = ref(JSON.parse(localStorage.getItem(LS_USER) || 'null'))
  const displayName = computed(() => user.value?.username || '同学')
  const isStudent = computed(() => user.value?.role === 'student')

  // 登录时效（token 过期/失效）标记：App.vue 据此弹出「重新登录」弹窗。
  // 由 api/client.js 的 401 拦截器置 true，重认证成功后由调用方置 false。
  const sessionExpired = ref(false)

  function setToken(t) {
    token.value = t
    if (t) localStorage.setItem(LS_TOKEN, t)
    else localStorage.removeItem(LS_TOKEN)
  }

  async function login(username, password) {
    const res = await apiLogin(username, password)
    setToken(res.access_token)
    user.value = { username, role: res.role }
    localStorage.setItem(LS_USER, JSON.stringify(user.value))
    return res
  }

  // 弹窗内的重新登录：与 login 同链路（本次实现仅账号密码；微信扫码待实现）。
  // 成功后由 LoginModal 调用 flushUnauthorized() 重放挂起请求。
  async function reauth(username, password) {
    return login(username, password)
  }

  // 用户放弃重认证（返回登录页）：清空时效态与本地凭证。
  function cancelReauth() {
    sessionExpired.value = false
    logout()
  }

  function logout() {
    token.value = ''
    user.value = null
    sessionExpired.value = false
    localStorage.removeItem(LS_TOKEN)
    localStorage.removeItem(LS_USER)
  }

  return {
    token,
    user,
    displayName,
    isStudent,
    sessionExpired,
    login,
    reauth,
    cancelReauth,
    logout,
  }
})
