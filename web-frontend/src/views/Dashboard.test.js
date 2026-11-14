import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import Dashboard from './Dashboard.vue'
import { useAuthStore } from '../stores/auth'
import { DEMO_STUDENT_ID, listWrongQuestions } from '../api/admin'

// 首页统计走真实接口，单测里只关心排版，桩掉避免发起请求
vi.mock('../api/admin', () => ({
  listKnowledge: vi.fn().mockResolvedValue([]),
  listWrongQuestions: vi.fn().mockResolvedValue([]),
  listCompliance: vi.fn().mockResolvedValue([]),
  DEMO_STUDENT_ID: 'student@tes.edu',
}))

// 后端菜单 path → 前端路由的映射见 utils/menu.js MENU_ROUTE_MAP
const DIR = {
  menu_id: 'm_ops', parent_id: null, menu_name: '区域运营',
  menu_type: 'DIR', path: '/ops', sort: 1,
}
const MODULE_PATHS = [
  ['/ops/knowledge', '教研内容管理'],
  ['/ops/analytics', '学情看板'],
  ['/ops/wrong', '错题本'],
  ['/ops/exam', '自动组卷'],
  ['/ops/grading', '教师批改'],
  ['/ops/reports', '家长周报'],
  ['/ops/compliance', '合规审核'],
  ['/ops/practice', '在线练习'],
]
const CRAWL_PATH = ['/ops/course-intake', '课程采集与知识点']

const menusOf = (paths) => [
  DIR,
  ...paths.map(([path, menu_name], i) => ({
    menu_id: `m_${i}`, parent_id: 'm_ops', menu_name,
    menu_type: 'MENU', path, sort: i + 2,
  })),
]
const ALL_MENUS = menusOf([...MODULE_PATHS, CRAWL_PATH])
const NO_GRADING = menusOf([
  ...MODULE_PATHS.filter(([p]) => p !== '/ops/grading'), CRAWL_PATH,
])
const PARENT_MENUS = menusOf([['/ops/reports', '家长周报']])

async function mountDashboard(menus, menusLoaded = true) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({
    user: { username: 'pilot@school01.edu', role: 'school' },
    menus, perms: [], menusLoaded,
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div>page</div>' } }],
  })
  router.push('/dashboard')
  await router.isReady()
  const wrapper = mount(Dashboard, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return wrapper
}

const labels = (wrapper) =>
  wrapper.findAll('.module .m-label').map((el) => el.text())

describe('Dashboard 首页排版', () => {
  it('全授权时快速入口 8 张卡片，采集工作台独立主推入口', async () => {
    const wrapper = await mountDashboard(ALL_MENUS)
    expect(wrapper.findAll('.module')).toHaveLength(8)
    const entry = wrapper.find('.crawl-entry')
    expect(entry.exists()).toBe(true)
    expect(entry.attributes('href')).toBe('/course-intake/workbench')
    expect(entry.text()).toContain('系统化任务采集')
    // 主推入口不混进卡片网格，卡片里不应重复出现
    expect(labels(wrapper)).not.toContain('系统化任务采集')
  })

  it('错题数用真实演示学生查询，不再写死库里 0 行的 student_001', async () => {
    await mountDashboard(ALL_MENUS)
    expect(listWrongQuestions).toHaveBeenCalledWith(DEMO_STUDENT_ID)
    expect(DEMO_STUDENT_ID).not.toBe('student_001')
  })

  it('菜单撤掉教师批改后，首页同步不再显示该卡片', async () => {
    const wrapper = await mountDashboard(NO_GRADING)
    expect(labels(wrapper)).not.toContain('教师批改')
    expect(labels(wrapper)).toContain('教研内容管理')
    expect(wrapper.findAll('.module')).toHaveLength(7)
    // 采集主推入口仍按自身授权显示，不受卡片过滤影响
    expect(wrapper.find('.crawl-entry').exists()).toBe(true)
  })

  it('家长只有周报授权时只显示家长周报卡片，主推入口隐藏', async () => {
    const wrapper = await mountDashboard(PARENT_MENUS)
    expect(labels(wrapper)).toEqual(['家长周报'])
    expect(wrapper.find('.crawl-entry').exists()).toBe(false)
  })

  it('授权未就绪时不提前隐藏卡片（首屏不闪空白）', async () => {
    const wrapper = await mountDashboard(PARENT_MENUS, false)
    expect(wrapper.findAll('.module')).toHaveLength(8)
  })
})
