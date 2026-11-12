import { describe, it, expect } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import CrawlLayout from './CrawlLayout.vue'
import { useAuthStore } from '../stores/auth'

const stub = { template: '<div>page</div>' }
const CHILDREN = [
  'workbench', 'sources', 'jobs', 'parse', 'review', 'runs',
].map((p) => ({ path: p, component: stub }))

async function mountAt(path) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({
    user: { username: 'pilot@school01.edu', role: 'school' },
    menus: [], perms: [], menusLoaded: true,
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', component: stub },
      { path: '/login', component: stub },
      {
        path: '/course-intake',
        component: CrawlLayout,
        redirect: '/course-intake/workbench',
        children: CHILDREN,
      },
    ],
  })
  router.push(path)
  await router.isReady()
  // 通过宿主 router-view 挂载：router-view 深度 0 渲染 CrawlLayout，
  // 其内部 router-view 深度 1 渲染子页。若直接 mount(CrawlLayout) 会
  // 让同一布局被渲染两遍（父路由组件 + 手动挂载）。
  const Host = { template: '<router-view />' }
  const wrapper = mount(Host, { global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('CrawlLayout 系统化采集布局', () => {
  it('顶栏三个一级域，当前域高亮', async () => {
    const { wrapper } = await mountAt('/course-intake/workbench')
    const tabs = wrapper.findAll('.top-tab')
    expect(tabs.map((t) => t.text())).toEqual(['业务中心', '解析审核', '运行监控'])
    expect(wrapper.find('.top-tab.on').text()).toBe('业务中心')
  })

  it('左侧渲染当前域二级菜单，面包屑显示域/项', async () => {
    const { wrapper } = await mountAt('/course-intake/workbench')
    expect(wrapper.findAll('a.side-item').map((a) => a.find('.side-label').text()))
      .toEqual(['采集工作台', '课程源管理', '采集任务'])
    expect(wrapper.find('.crumb').text()).toContain('业务中心')
    expect(wrapper.find('.crumb').text()).toContain('采集工作台')
  })

  it('切换一级域后菜单与高亮同步变化', async () => {
    const { wrapper, router } = await mountAt('/course-intake/workbench')
    await router.push('/course-intake/parse')
    await flushPromises()
    expect(wrapper.find('.top-tab.on').text()).toBe('解析审核')
    const labels = wrapper.findAll('.side-item').map((e) => e.find('.side-label').text())
    expect(labels).toContain('解析预览')
    expect(labels).toContain('知识点审核')
  })

  it('规划项灰显且不是链接，不产生死链', async () => {
    const { wrapper } = await mountAt('/course-intake/parse')
    const planned = wrapper.findAll('.side-item.planned')
    expect(planned.length).toBe(2)
    for (const el of planned) {
      expect(el.element.tagName).not.toBe('A')
      expect(el.attributes('href')).toBeUndefined()
      expect(el.text()).toContain('规划中')
    }
  })

  it('提供返回主站入口与退出按钮', async () => {
    const { wrapper } = await mountAt('/course-intake/runs')
    expect(wrapper.find('.back-main').attributes('href')).toBe('/dashboard')
    expect(wrapper.find('.logout').text()).toBe('退出')
  })
})
