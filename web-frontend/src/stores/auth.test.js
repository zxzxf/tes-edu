import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

// mock API 层：store 单测不发真实请求
vi.mock('../api/admin', () => ({ login: vi.fn() }))
vi.mock('../api/menu', () => ({ getMyMenus: vi.fn() }))

import { login as apiLogin } from '../api/admin'
import { getMyMenus } from '../api/menu'
import { useAuthStore } from './auth'

const FLAT = [
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
  { menu_id: 'm_ops_analytics', parent_id: 'm_ops', menu_name: '学情看板', menu_type: 'MENU', path: '/ops/analytics', sort: 3 },
  { menu_id: 'm_ops_exam', parent_id: 'm_ops', menu_name: '自动组卷', menu_type: 'MENU', path: '/ops/exam', sort: 5 },
  { menu_id: 'm_ops_exam_gen', parent_id: 'm_ops_exam', menu_name: '生成组卷', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'exam:generate' },
  { menu_id: 'm_ops_reports', parent_id: 'm_ops', menu_name: '家长周报', menu_type: 'MENU', path: '/ops/reports', sort: 7 },
]

function freshStore() {
  setActivePinia(createPinia())
  localStorage.clear()
  return useAuthStore()
}

describe('auth store：动态授权', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loadMenus 填充 menus/perms/树并写 localStorage', async () => {
    getMyMenus.mockResolvedValue(FLAT)
    const auth = freshStore()

    await auth.loadMenus()

    expect(auth.menusLoaded).toBe(true)
    expect(auth.perms).toEqual(['exam:generate'])
    expect(auth.menuTree.map((n) => n.menu_id)).toEqual(['m_ops'])
    expect(auth.menuTree[0].children.map((c) => c.menu_id)).toEqual([
      'm_ops_analytics', 'm_ops_exam', 'm_ops_reports',
    ])
    // 刷新秒开缓存
    expect(JSON.parse(localStorage.getItem('perms'))).toEqual(['exam:generate'])
    expect(JSON.parse(localStorage.getItem('menus'))).toHaveLength(FLAT.length)
  })

  it('login 成功后自动拉授权', async () => {
    apiLogin.mockResolvedValue({ access_token: 'tk', role: 'teacher' })
    getMyMenus.mockResolvedValue(FLAT)
    const auth = freshStore()

    await auth.login('teacher@tes.edu', 'demo1234')

    expect(auth.token).toBe('tk')
    expect(auth.user).toEqual({ username: 'teacher@tes.edu', role: 'teacher' })
    expect(getMyMenus).toHaveBeenCalledOnce()
    expect(auth.perms).toEqual(['exam:generate'])
  })

  it('hasPerm：admin 恒放行 / 精确 / 通配 / 拒绝', async () => {
    getMyMenus.mockResolvedValue([])
    const auth = freshStore()

    auth.$patch({ user: { role: 'admin' }, menusLoaded: true })
    expect(auth.hasPerm('exam:generate')).toBe(true)

    auth.$patch({ user: { role: 'teacher' }, perms: ['exam:generate'] })
    expect(auth.hasPerm('exam:generate')).toBe(true)
    expect(auth.hasPerm('grading:submit')).toBe(false)
    expect(auth.hasPerm('')).toBe(true)

    auth.$patch({ perms: ['exam:*'] })
    expect(auth.hasPerm('exam:generate')).toBe(true)
  })

  it('hasRouteAccess：映射路由按授权放行，概览常驻', async () => {
    getMyMenus.mockResolvedValue(FLAT)
    const auth = freshStore()
    await auth.loadMenus()

    expect(auth.hasRouteAccess('/dashboard')).toBe(true)
    expect(auth.hasRouteAccess('/analytics')).toBe(true)
    expect(auth.hasRouteAccess('/reports')).toBe(true)
    expect(auth.hasRouteAccess('/knowledge')).toBe(false) // 未授权菜单映射
  })

  it('hasRouteAccess：授权未就绪/空菜单不拦（后端才是边界）', () => {
    const auth = freshStore()
    expect(auth.hasRouteAccess('/knowledge')).toBe(true)
    auth.$patch({ menusLoaded: true, menus: [] })
    expect(auth.hasRouteAccess('/knowledge')).toBe(true)
  })

  it('logout 清空 token/用户/菜单/权限', async () => {
    getMyMenus.mockResolvedValue(FLAT)
    const auth = freshStore()
    await auth.loadMenus()
    expect(auth.perms.length).toBeGreaterThan(0)

    auth.logout()
    expect(auth.token).toBe('')
    expect(auth.user).toBe(null)
    expect(auth.menus).toEqual([])
    expect(auth.perms).toEqual([])
    expect(auth.menusLoaded).toBe(false)
    expect(localStorage.getItem('menus')).toBe(null)
    expect(localStorage.getItem('perms')).toBe(null)
  })
})
