import { describe, it, expect, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import Layout from './Layout.vue'
import { useAuthStore } from '../stores/auth'

// 教师视角的授权菜单（含 BUTTON 与无前端页面的 /system/menu）
const TEACHER_MENUS = [
  { menu_id: 'm_sys', parent_id: null, menu_name: '系统设置', menu_type: 'DIR', path: '/system', sort: 1 },
  { menu_id: 'm_sys_menu', parent_id: 'm_sys', menu_name: '菜单管理', menu_type: 'MENU', path: '/system/menu', sort: 1 },
  { menu_id: 'm_sys_region', parent_id: 'm_sys', menu_name: '区域管理', menu_type: 'MENU', path: '/system/region', sort: 3 },
  { menu_id: 'm_sys_vendor', parent_id: 'm_sys', menu_name: '系统服务(MCP厂商)', menu_type: 'MENU', path: '/system/vendor', sort: 4 },
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
  { menu_id: 'm_ops_knowledge', parent_id: 'm_ops', menu_name: '教研内容管理', menu_type: 'MENU', path: '/ops/knowledge', sort: 2 },
  { menu_id: 'm_ops_analytics', parent_id: 'm_ops', menu_name: '学情看板', menu_type: 'MENU', path: '/ops/analytics', sort: 3 },
  { menu_id: 'm_ops_tenant', parent_id: 'm_ops', menu_name: '学校管理', menu_type: 'MENU', path: '/ops/tenant', sort: 1 },
  { menu_id: 'm_ops_exam_gen', parent_id: 'm_ops_exam', menu_name: '生成组卷', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'exam:generate' },
]

async function mountLayout(role, menus) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'teacher@tes.edu', role }, menus, perms: ['exam:generate'], menusLoaded: true })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', redirect: '/dashboard' },
      { path: '/dashboard', name: 'dashboard', component: { template: '<div>dash</div>' }, meta: { title: '概览' } },
    ],
  })
  router.push('/dashboard')
  await router.isReady()

  const wrapper = mount(Layout, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

const navLabels = (wrapper) =>
  wrapper.findAll('a.nav-item .nav-label').map((el) => el.text())
const groupLabels = (wrapper) =>
  wrapper.findAll('.nav-group').map((el) => el.text())
const navHrefs = (wrapper) =>
  wrapper.findAll('a.nav-item').map((el) => el.attributes('href'))

describe('Layout 动态导航（T-AM7）', () => {
  it('按授权菜单渲染：概览常驻 + 动态组，剔除无页面菜单与 BUTTON', async () => {
    const wrapper = await mountLayout('teacher', TEACHER_MENUS)

    // T-AM3~T-AM6 前端管理页建好后，/system/menu、/system/region、/system/vendor 有路由映射，
    // 系统设置组随授权菜单出现；T-AM9 学校管理页已建，/ops/tenant 解析为 /tenant 出现在导航
    expect(navLabels(wrapper)).toEqual([
      '概览', '菜单管理', '区域管理', '系统服务(MCP厂商)', '学校管理', '教研内容管理', '学情看板',
    ])
    expect(groupLabels(wrapper)).toEqual(['系统设置', '区域运营'])
    expect(navHrefs(wrapper)).toEqual([
      '/dashboard', '/system/menu', '/system/region', '/system/vendor', '/tenant', '/knowledge', '/analytics',
    ])
    // BUTTON 不进导航；无前端页面的菜单不出现在导航（生成组卷是 BUTTON，不出现）
    expect(wrapper.text()).not.toContain('生成组卷')
    // 学校管理页已建，授权内且可导航 → 出现
    expect(wrapper.text()).toContain('学校管理')
  })

  it('角色文案显示五角色映射', async () => {
    const wrapper = await mountLayout('region_admin', TEACHER_MENUS)
    expect(wrapper.find('.role-chip').text()).toBe('区域管理员')
  })

  it('家长视角：仅概览 + 家长周报', async () => {
    const parentMenus = [
      { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
      { menu_id: 'm_ops_reports', parent_id: 'm_ops', menu_name: '家长周报', menu_type: 'MENU', path: '/ops/reports', sort: 7 },
    ]
    const wrapper = await mountLayout('parent', parentMenus)
    expect(navLabels(wrapper)).toEqual(['概览', '家长周报'])
    expect(groupLabels(wrapper)).toEqual(['区域运营'])
    expect(navHrefs(wrapper)).toEqual(['/dashboard', '/reports'])
  })

  // 侧栏溢出治理：菜单多时靠 .nav 独立滚动 + 省略号，故每项需带 title 供悬浮看全文
  it('导航项与分组标题带 title（长文案截断后可悬浮查看全文）', async () => {
    const wrapper = await mountLayout('teacher', TEACHER_MENUS)
    const items = wrapper.findAll('a.nav-item')
    expect(items.length).toBeGreaterThan(0)
    for (const el of items) {
      expect(el.attributes('title')).toBe(el.find('.nav-label').text())
    }
    // 分组标题同样带 title
    for (const el of wrapper.findAll('.nav-group')) {
      expect(el.attributes('title')).toBe(el.text())
    }
  })

  it('菜单再多也只渲染一个滚动容器 .nav（溢出靠滚动而非裁切）', async () => {
    // 用映射表里真实存在的不同 path 构造 10 项，模拟 admin 视角菜单过多
    const OPS_PATHS = [
      '/ops/tenant', '/ops/knowledge', '/ops/analytics', '/ops/error-distribution',
      '/ops/wrong', '/ops/exam', '/ops/grading', '/ops/reports', '/ops/compliance', '/ops/practice',
    ]
    const many = [
      { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 1 },
      ...OPS_PATHS.map((p, i) => ({
        menu_id: `m_ops_x${i}`, parent_id: 'm_ops', menu_name: `菜单${i}`,
        menu_type: 'MENU', path: p, sort: i,
      })),
    ]
    const wrapper = await mountLayout('admin', many)
    expect(wrapper.findAll('.nav')).toHaveLength(1)
    expect(wrapper.findAll('a.nav-item')).toHaveLength(11) // 概览 + 10 项
    expect(wrapper.findAll('.nav-group')).toHaveLength(1)
    // 每项 to 唯一（无重复 key）
    const hrefs = navHrefs(wrapper)
    expect(new Set(hrefs).size).toBe(hrefs.length)
  })
})
