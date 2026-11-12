/**
 * 系统化任务采集工作台的导航配置（M5 课程采集与知识体系拆分）。
 *
 * 布局：顶部一级域 + 左侧二级菜单 + 右侧工作区，对齐《采集任务方案计划书》。
 * 只把已有真实后端接口的功能登记为可点路由（ready）；尚未实现的规划项
 * 标 planned=true，由 CrawlLayout 灰显并打「规划中」，不造假页面、不产生死链。
 *
 * 全部为纯数据 + 纯函数，可单测（见 crawlNav.test.js）。
 */

export const CRAWL_NAV = [
  {
    key: 'center',
    label: '业务中心',
    items: [
      { label: '采集工作台', path: '/course-intake/workbench', m5: 'M5-6' },
      { label: '课程源管理', path: '/course-intake/sources', m5: 'M5-1' },
      { label: '采集任务', path: '/course-intake/jobs', m5: 'M5-1' },
    ],
  },
  {
    key: 'review',
    label: '解析审核',
    items: [
      { label: '解析预览', path: '/course-intake/parse', m5: 'M5-2' },
      { label: '知识点审核', path: '/course-intake/review', m5: 'M5-3 / M5-4' },
      { label: '知识点图谱', planned: true, m5: 'M5-3' },
      { label: '发布记录', planned: true, m5: 'M5-4' },
    ],
  },
  {
    key: 'ops',
    label: '运行监控',
    items: [
      { label: '运行日志', path: '/course-intake/runs', m5: 'M5-6' },
      { label: '预算策略', planned: true, m5: 'M5-5' },
      { label: '版权授权清单', planned: true, m5: 'M5-5' },
      { label: '告警规则', planned: true, m5: 'M5-6' },
    ],
  },
]

/** 按路由 path 找当前一级域与二级项；未命中返回首个域作兜底 */
export function findCrawlNav(path) {
  for (const group of CRAWL_NAV) {
    const item = group.items.find((it) => it.path === path)
    if (item) return { group, item }
  }
  return { group: CRAWL_NAV[0], item: CRAWL_NAV[0].items[0] }
}

/** 一级域首个可点页面（顶栏切换时跳转目标） */
export function firstReadyPath(group) {
  const hit = (group.items || []).find((it) => !it.planned && it.path)
  return hit ? hit.path : null
}
