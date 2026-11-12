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
  { menu_id: 'm_ops_tenant', parent_id: 'm_ops', menu_name: '学校管理', menu_type: 'MENU', path: '/ops/tenant', sort: 1 },
  { menu_id: 'm_ops_knowledge', parent_id: 'm_ops', menu_name: '教研内容管理', menu_type: 'MENU', path: '/ops/knowledge', sort: 2 },
  { menu_id: 'm_ops_analytics', parent_id: 'm_ops', menu_name: '学情看板', menu_type: 'MENU', path: '/ops/analytics', sort: 3 },
  { menu_id: 'm_ops_course_intake', parent_id: 'm_ops', menu_name: '课程采集与知识点', menu_type: 'MENU', path: '/ops/course-intake', sort: 4 },
  { menu_id: 'm_ops_exam_gen', parent_id: 'm_ops_exam', menu_name: '生成组卷', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'exam:generate' },
]

async function mountLayout(role, menus, initialPath = '/dashboard') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'teacher@tes.edu', role }, menus, perms: ['exam:generate'], menusLoaded: true })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', redirect: '/dashboard' },
      { path: '/dashboard', name: 'dashboard', component: { template: '<div>dash</div>' }, meta: { title: '概览' } },
      // 扁平兜底：深链测试只关心导航态，页面内容由 stub 承载。
      // 注意不能把 Layout 挂进测试路由，否则 mount(Layout) + <router-view> 会渲染两遍壳子。
      { path: '/:pathMatch(.*)*', component: { template: '<div>page</div>' } },
    ],
  })
  router.push(initialPath)
  await router.isReady()

  const wrapper = mount(Layout, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

const navLabels = (wrapper) =>
  wrapper.findAll('a.nav-item .nav-label').map((el) => el.text())
const groupLabels = (wrapper) =>
  wrapper.findAll('nav.side-nav .nav-group .nav-group-label').map((el) => el.text())
const navHrefs = (wrapper) =>
  wrapper.findAll('a.nav-item').map((el) => el.attributes('href'))
const topTabs = (wrapper) =>
  wrapper.findAll('.top-tab').map((el) => el.text())
const dividers = (wrapper) =>
  wrapper.findAll('.side-divider').map((el) => el.text())
const clickTab = async (wrapper, label) => {
  const tab = wrapper.findAll('.top-tab').find((el) => el.text() === label)
  await tab.trigger('click')
  await flushPromises()
}

describe('Layout 全站三段式（顶栏一级域 + 左侧二级 + 右侧工作区）', () => {
  it('顶栏只有概览 + 采集三域，授权分组降为概览下的二级标题', async () => {
    const { wrapper } = await mountLayout('teacher', TEACHER_MENUS)
    expect(topTabs(wrapper)).toEqual(['概览', '业务中心', '解析审核', '运行监控'])
    expect(topTabs(wrapper)).not.toContain('区域运营')
    expect(topTabs(wrapper)).not.toContain('系统设置')
    expect(wrapper.find('.top-tab.on').text()).toBe('概览')
    expect(dividers(wrapper)).toEqual(['系统设置', '区域运营'])
    // 分组标题必须排在常驻「概览」项下面
    const children = wrapper.find('nav.side-nav').element.children
    expect(children[0].textContent).toContain('概览')
    expect(children[1].classList.contains('side-divider')).toBe(true)
    expect(groupLabels(wrapper)).toEqual([])
    expect(wrapper.find('.crumb').text()).toContain('概览')
    expect(wrapper.find('.app-top').exists()).toBe(true)
    expect(wrapper.find('.app-side').exists()).toBe(true)
    expect(wrapper.find('.app-main').exists()).toBe(true)
  })

  it('概览域左侧按授权渲染全部二级项，剔除 BUTTON 与无页面菜单', async () => {
    const { wrapper } = await mountLayout('teacher', TEACHER_MENUS, '/knowledge')
    expect(wrapper.find('.top-tab.on').text()).toBe('概览')
    expect(navLabels(wrapper)).toEqual([
      '概览',
      '菜单管理', '区域管理', '系统服务(MCP厂商)',
      '学校管理', '教研内容管理', '学情看板', '课程采集与知识点',
    ])
    expect(navHrefs(wrapper)).toEqual([
      '/dashboard', '/system/menu', '/system/region', '/system/vendor',
      '/tenant', '/knowledge', '/analytics', '/course-intake',
    ])
    expect(dividers(wrapper)).toEqual(['系统设置', '区域运营'])
    expect(wrapper.text()).not.toContain('生成组卷')
  })

  it('角色文案显示五角色映射', async () => {
    const { wrapper } = await mountLayout('region_admin', TEACHER_MENUS)
    expect(wrapper.find('.role-chip').text()).toBe('区域管理员')
  })

  it('家长视角：无采集授权则顶栏只有概览，区域运营在左侧概览下', async () => {
    const parentMenus = [
      { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
      { menu_id: 'm_ops_reports', parent_id: 'm_ops', menu_name: '家长周报', menu_type: 'MENU', path: '/ops/reports', sort: 7 },
    ]
    const { wrapper } = await mountLayout('parent', parentMenus, '/reports')
    expect(topTabs(wrapper)).toEqual(['概览'])
    expect(dividers(wrapper)).toEqual(['区域运营'])
    expect(navLabels(wrapper)).toEqual(['概览', '家长周报'])
    expect(navHrefs(wrapper)).toEqual(['/dashboard', '/reports'])
  })

  it('二级项与域标题带 title（长文案截断后可悬浮查看全文）', async () => {
    const { wrapper } = await mountLayout('teacher', TEACHER_MENUS, '/knowledge')
    const items = wrapper.findAll('a.nav-item')
    expect(items.length).toBeGreaterThan(0)
    for (const el of items) {
      expect(el.attributes('title')).toBe(el.find('.nav-label').text())
    }
    const title = wrapper.find('.side-title')
    expect(title.attributes('title')).toBe(title.find('.nav-group-label').text())
    // 分组标题是纯文本，不是链接、不产生死链
    for (const el of wrapper.findAll('.side-divider')) {
      expect(el.element.tagName).not.toBe('A')
      expect(el.attributes('href')).toBeUndefined()
    }
  })

  it('菜单再多也只渲染一个滚动容器 .nav（溢出靠滚动而非裁切）', async () => {
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
    const { wrapper } = await mountLayout('admin', many, '/knowledge')
    expect(wrapper.findAll('.nav')).toHaveLength(1)
    expect(wrapper.findAll('a.nav-item')).toHaveLength(11) // 常驻概览 + 10 项
    expect(wrapper.findAll('.side-divider')).toHaveLength(1)
    const hrefs = navHrefs(wrapper)
    expect(new Set(hrefs).size).toBe(hrefs.length)
  })

  it('点顶栏一级域跳转到该域首个可用页，菜单与高亮同步切换', async () => {
    const { wrapper, router } = await mountLayout('teacher', TEACHER_MENUS)
    await clickTab(wrapper, '解析审核')
    expect(router.currentRoute.value.path).toBe('/course-intake/parse')
    expect(wrapper.find('.top-tab.on').text()).toBe('解析审核')
    expect(navLabels(wrapper)).toContain('解析预览')
    expect(navLabels(wrapper)).toContain('知识点审核')
    expect(wrapper.find('.crumb').text()).toContain('解析审核')
    // 点回概览：左侧恢复授权菜单分组
    await clickTab(wrapper, '概览')
    expect(router.currentRoute.value.path).toBe('/dashboard')
    expect(dividers(wrapper)).toEqual(['系统设置', '区域运营'])
  })

  it('采集域规划项灰显且不是链接，不产生死链', async () => {
    const { wrapper } = await mountLayout('teacher', TEACHER_MENUS, '/course-intake/runs')
    expect(wrapper.find('.top-tab.on').text()).toBe('运行监控')
    const planned = wrapper.findAll('.side-item.planned')
    expect(planned.length).toBe(3)
    for (const el of planned) {
      expect(el.element.tagName).not.toBe('A')
      expect(el.attributes('href')).toBeUndefined()
      expect(el.text()).toContain('规划中')
    }
    expect(navLabels(wrapper)).toContain('运行日志')
  })
})

describe('Layout 二级菜单折叠', () => {
  beforeEach(() => {
    localStorage.removeItem('tes_nav_closed_groups')
  })

  it('默认全展开；点域标题只收起当前域，切到别的域仍正常显示', async () => {
    const { wrapper, router } = await mountLayout('teacher', TEACHER_MENUS, '/knowledge')
    expect(navLabels(wrapper)).toContain('教研内容管理')

    const g = wrapper.find('.side-title')
    expect(g.classes()).not.toContain('closed')
    expect(g.attributes('aria-expanded')).toBe('true')
    await g.trigger('click')
    expect(wrapper.find('.side-title').classes()).toContain('closed')
    expect(wrapper.find('.side-title').attributes('aria-expanded')).toBe('false')
    expect(navLabels(wrapper)).toEqual([])
    expect(dividers(wrapper)).toEqual([])
    expect(JSON.parse(localStorage.getItem('tes_nav_closed_groups'))).toEqual(['home'])

    // 折叠状态按域记：切到采集域，其菜单照常显示
    await router.push('/course-intake/workbench')
    await flushPromises()
    expect(wrapper.find('.top-tab.on').text()).toBe('业务中心')
    expect(navLabels(wrapper)).toEqual(['采集工作台', '课程源管理', '采集任务'])
  })

  it('再点一次恢复，退出按钮可用', async () => {
    const { wrapper } = await mountLayout('teacher', TEACHER_MENUS, '/knowledge')
    await wrapper.find('.side-title').trigger('click')
    await wrapper.find('.side-title').trigger('click')
    expect(navLabels(wrapper)).toContain('教研内容管理')
    expect(JSON.parse(localStorage.getItem('tes_nav_closed_groups'))).toEqual([])
    expect(wrapper.find('.logout').text()).toBe('退出')
  })

  it('折叠状态按域隔离；深链进入被收起的域时自动展开该域', async () => {
    localStorage.setItem('tes_nav_closed_groups', JSON.stringify(['crawl-review']))

    const w1 = await mountLayout('teacher', TEACHER_MENUS, '/dashboard')
    expect(navLabels(w1.wrapper)).toContain('教研内容管理')

    const w2 = await mountLayout('teacher', TEACHER_MENUS, '/course-intake/parse')
    expect(navLabels(w2.wrapper)).toContain('解析预览')
    expect(w2.wrapper.find('.side-title').attributes('aria-expanded')).toBe('true')
    expect(JSON.parse(localStorage.getItem('tes_nav_closed_groups'))).toEqual([])
  })
})
