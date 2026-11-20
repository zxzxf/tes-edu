/**
 * 股票盯盘业务域导航配置（股票盯盘业务中心 v1.1）。
 *
 * 布局：顶部一级域「股票盯盘」+ 左侧二级菜单 + 右侧工作区。
 * 仅 admin 可见，RBAC 门禁由 auth.hasRouteAccess 控制。
 *
 * 全部为纯数据 + 纯函数，可单测。
 */

export const STOCK_NAV = [
  {
    key: 'stock',
    label: '股票盯盘',
    items: [
      { label: '盯盘总览', path: '/stock/overview' },
      { label: '自选股', path: '/stock/watchlist' },
      { label: '行情看板', path: '/stock/quotes' },
      { label: '涨跌预警', path: '/stock/alerts' },
      { label: '推送好友', path: '/stock/friends' },
      { label: '个股详情', path: '/stock/detail' },
    ],
  },
]

/** 按路由 path 找当前一级域与二级项；未命中返回首个域作兜底 */
export function findStockNav(path) {
  for (const group of STOCK_NAV) {
    const item = group.items.find((it) => it.path === path)
    if (item) return { group, item }
  }
  return { group: STOCK_NAV[0], item: STOCK_NAV[0].items[0] }
}

/** 一级域首个可点页面（顶栏切换时跳转目标） */
export function firstStockReadyPath(group) {
  const hit = (group.items || []).find((it) => !it.planned && it.path)
  return hit ? hit.path : null
}
