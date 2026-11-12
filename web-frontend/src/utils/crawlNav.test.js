import { describe, it, expect } from 'vitest'
import { CRAWL_NAV, findCrawlNav, firstReadyPath } from './crawlNav'

describe('采集工作台导航配置', () => {
  it('三个一级域，与截图的顶栏规模一致', () => {
    expect(CRAWL_NAV.map((g) => g.label)).toEqual(['业务中心', '解析审核', '运行监控'])
  })

  it('每个一级域至少有一个可点页面，顶栏点过去不会落空', () => {
    for (const g of CRAWL_NAV) {
      expect(firstReadyPath(g)).toBeTruthy()
    }
  })

  it('规划项不携带 path，避免生成死链', () => {
    const planned = CRAWL_NAV.flatMap((g) => g.items).filter((it) => it.planned)
    expect(planned.length).toBeGreaterThan(0)
    for (const it of planned) {
      expect(it.path).toBeUndefined()
      expect(it.m5).toBeTruthy()
    }
  })

  it('所有可点路径唯一且都在 /course-intake 前缀下', () => {
    const paths = CRAWL_NAV.flatMap((g) => g.items)
      .filter((it) => !it.planned).map((it) => it.path)
    expect(new Set(paths).size).toBe(paths.length)
    for (const p of paths) expect(p.startsWith('/course-intake/')).toBe(true)
  })

  it('findCrawlNav 按路径定位所属一级域与二级项', () => {
    const hit = findCrawlNav('/course-intake/review')
    expect(hit.group.label).toBe('解析审核')
    expect(hit.item.label).toBe('知识点审核')
    expect(hit.item.m5).toBe('M5-3 / M5-4')
  })

  it('未知路径兜底到业务中心 / 采集工作台', () => {
    const hit = findCrawlNav('/unknown/path')
    expect(hit.group.key).toBe('center')
    expect(hit.item.label).toBe('采集工作台')
  })

  it('firstReadyPath 跳过规划项', () => {
    const g = { items: [
      { label: '规划', planned: true },
      { label: '可用', path: '/course-intake/workbench' },
    ] }
    expect(firstReadyPath(g)).toBe('/course-intake/workbench')
    expect(firstReadyPath({ items: [{ planned: true }] })).toBeNull()
  })
})
