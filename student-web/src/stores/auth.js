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

  async function login(username, password) {
    const res = await apiLogin(username, password)
    token.value = res.access_token
    user.value = { username, role: res.role }
    localStorage.setItem(LS_TOKEN, token.value)
    localStorage.setItem(LS_USER, JSON.stringify(user.value))
    return res
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem(LS_TOKEN)
    localStorage.removeItem(LS_USER)
  }

  return { token, user, displayName, isStudent, login, logout }
})
