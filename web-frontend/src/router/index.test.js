import { describe, it, expect } from 'vitest'
import router from './index'
import Layout from '../views/Layout.vue'
import CrawlLayout from '../views/CrawlLayout.vue'
import Workbench from '../views/crawl/Workbench.vue'

describe('系统化采集工作台路由', () => {
  it('六个子页与主站共用统一三段式 Layout（无双层壳子、无独立 CrawlLayout）', () => {
    for (const p of ['workbench', 'sources', 'jobs', 'parse', 'review', 'runs']) {
      const r = router.resolve(`/course-intake/${p}`)
      const comps = r.matched.map((m) => m.components?.default)
      expect(comps).toContain(Layout)
      expect(comps.filter((c) => c === Layout)).toHaveLength(1)
      expect(comps).not.toContain(CrawlLayout)
    }
  })

  it('/course-intake 重定向到工作台首页，主站菜单链接不断', () => {
    const r = router.resolve('/course-intake')
    expect(r.redirectedFrom).toBeUndefined()
    const parent = router.getRoutes().find((x) => x.path === '/course-intake')
    expect(parent.redirect).toBe('/course-intake/workbench')
    expect(router.resolve('/course-intake/workbench').matched
      .map((m) => m.components?.default)).toContain(Workbench)
  })

  it('主站其余页面仍走 Layout', () => {
    expect(router.resolve('/dashboard').matched
      .map((m) => m.components?.default)).toContain(Layout)
  })
})
