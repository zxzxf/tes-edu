import { describe, it, expect } from 'vitest'
import { STOCK_NAV, findStockNav, firstStockReadyPath } from './stockNav'

// 股票盯盘域导航（docx/股票盯盘业务中心实施计划.md v1.4 阶段 1 验收锁）
describe('股票盯盘导航配置', () => {
  it('一个一级域「股票盯盘」', () => {
    expect(STOCK_NAV.map((g) => g.label)).toEqual(['股票盯盘'])
  })

  it('五个可点页面 + 一个规划项（个股详情）', () => {
    const items = STOCK_NAV[0].items
    const ready = items.filter((it) => !it.planned)
    expect(ready.map((it) => it.label)).toEqual(['盯盘总览', '自选股', '行情看板', '涨跌预警', '推送好友'])
    expect(items.some((it) => it.planned)).toBe(true)
  })

  it('可点路径全部在 /stock 前缀下且唯一', () => {
    const paths = STOCK_NAV.flatMap((g) => g.items).filter((it) => !it.planned).map((it) => it.path)
    expect(new Set(paths).size).toBe(paths.length)
    for (const p of paths) expect(p.startsWith('/stock/')).toBe(true)
  })

  it('规划项不带 path，避免死链', () => {
    for (const it of STOCK_NAV.flatMap((g) => g.items).filter((x) => x.planned)) {
      expect(it.path).toBeUndefined()
    }
  })

  it('首域首点为盯盘总览（顶栏切换目标）', () => {
    expect(firstStockReadyPath(STOCK_NAV[0])).toBe('/stock/overview')
  })

  it('按路径精确命中当前二级项', () => {
    expect(findStockNav('/stock/friends').item.label).toBe('推送好友')
    // 未命中回退首项（面包屑不炸）
    expect(findStockNav('/stock/unknown').item.path).toBe('/stock/overview')
  })
})
