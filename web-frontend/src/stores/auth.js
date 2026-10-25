import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { login as apiLogin } from '../api/admin'
import { getMyMenus } from '../api/menu'
import { buildMenuTree, collectPerms, matchPerm, routePathOf } from '../utils/menu'

// T-AM7：auth store 从「token+user」扩展为「token+user+授权菜单+按钮权限码」。
// 菜单/权限码源自后端 /menu/my（sys_role_menu 聚合），前端不再硬编码角色矩阵；
// localStorage 缓存供刷新秒开，路由守卫在首屏会重新拉取保证新鲜。

const LS_MENUS = 'menus'
const LS_PERMS = 'perms'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(JSON.parse(localStorage.getItem('user') || 'null'))
  const menus = ref(JSON.parse(localStorage.getItem(LS_MENUS) || '[]'))
  const perms = ref(JSON.parse(localStorage.getItem(LS_PERMS) || '[]'))
  // 本次 SPA 会话是否已从后端拉到授权（刷新后为 false，由路由守卫补拉）
  const menusLoaded = ref(false)

  const menuTree = computed(() => buildMenuTree(menus.value))
  const isAdmin = computed(() => user.value?.role === 'admin')

  /** 授权可达的前端路由集合：菜单映射 + 概览常驻（无对应菜单节点） */
  const allowedRoutes = computed(() => {
    const set = new Set(['/dashboard'])
    for (const m of menus.value) {
      const to = routePathOf(m)
      if (to) set.add(to)
    }
    return set
  })

  async function login(username, password) {
    const res = await apiLogin(username, password)
    token.value = res.access_token
    user.value = { username, role: res.role }
    localStorage.setItem('token', token.value)
    localStorage.setItem('user', JSON.stringify(user.value))
    // 登录后立即拉授权：菜单/权限码就绪后再进工作台
    await loadMenus()
    return res
  }

  async function loadMenus() {
    const rows = await getMyMenus()
    menus.value = Array.isArray(rows) ? rows : []
    perms.value = collectPerms(menus.value)
    localStorage.setItem(LS_MENUS, JSON.stringify(menus.value))
    localStorage.setItem(LS_PERMS, JSON.stringify(perms.value))
    menusLoaded.value = true
    return menus.value
  }

  /** 按钮级权限校验（镜像后端 has_perm；admin 恒放行） */
  function hasPerm(code) {
    if (!code) return true
    if (isAdmin.value) return true
    if (!menusLoaded.value) return true // 授权未就绪不误伤，交由路由守卫保证加载
    return matchPerm(perms.value, code)
  }

  /**
   * 路由级访问校验（导航门禁，UX 层）：
   * - 概览与未映射菜单的路由不拦
   * - 授权未就绪/菜单为空（如无角色用户）不拦——后端 require_role/require_perm
   *   才是安全边界，此处只做「别把用户导向一堆 403 页面」的体验优化
   */
  function hasRouteAccess(path) {
    if (!menusLoaded.value || menus.value.length === 0) return true
    return allowedRoutes.value.has(path)
  }

  function logout() {
    token.value = ''
    user.value = null
    menus.value = []
    perms.value = []
    menusLoaded.value = false
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem(LS_MENUS)
    localStorage.removeItem(LS_PERMS)
  }

  return {
    token, user, menus, perms, menuTree, menusLoaded, isAdmin, allowedRoutes,
    login, loadMenus, hasPerm, hasRouteAccess, logout,
  }
})
