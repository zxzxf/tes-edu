import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import Dashboard from './Dashboard.vue'
import { useAuthStore } from '../stores/auth'

// 首页统计走真实接口，单测里只关心排版，桩掉避免发起请求
vi.mock('../api/admin', () => ({
  listKnowledge: vi.fn().mockResolvedValue([]),
  listWrongQuestions: vi.fn().mockResolvedValue([]),
  listCompliance: vi.fn().mockResolvedValue([]),
}))

const WITH_CRAWL = [
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 1 },
  { menu_id: 'm_ops_course_intake', parent_id: 'm_ops', menu_name: '课程采集与知识点', menu_type: 'MENU', path: '/ops/course-intake', sort: 1 },
]
const NO_CRAWL = [
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 1 },
  { menu_id: 'm_ops_knowledge', parent_id: 'm_ops', menu_name: '教研内容管理', menu_type: 'MENU', path: '/ops/knowledge', sort: 1 },
]

async function mountDashboard(menus) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({
    user: { username: 'pilot@school01.edu', role: 'school' },
    menus, perms: [], menusLoaded: true,
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

describe('Dashboard 首页排版', () => {
  it('快速入口固定 8 张卡片（4×2），采集工作台独立主推入口', async () => {
    const wrapper = await mountDashboard(WITH_CRAWL)
    expect(wrapper.findAll('.module')).toHaveLength(8)
    const entry = wrapper.find('.crawl-entry')
    expect(entry.exists()).toBe(true)
    expect(entry.attributes('href')).toBe('/course-intake/workbench')
    expect(entry.text()).toContain('系统化任务采集')
    // 主推入口不混进卡片网格，卡片里不应重复出现
    expect(wrapper.findAll('.module .m-label').map((el) => el.text()))
      .not.toContain('系统化任务采集')
  })

  it('无采集授权时主推入口隐藏，快速入口仍是 8 张', async () => {
    const wrapper = await mountDashboard(NO_CRAWL)
    expect(wrapper.find('.crawl-entry').exists()).toBe(false)
    expect(wrapper.findAll('.module')).toHaveLength(8)
  })
})
