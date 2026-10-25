/**
 * 菜单纯函数层（T-AM7）：配套后端 GET /menu/my 的扁平列表。
 *
 * 后端返回扁平数组（DIR/MENU/BUTTON 三类节点，parent_id 挂载），前端负责：
 * - buildMenuTree  按 parent_id 建树（BUTTON 是权限码载体，不进导航）
 * - collectPerms   聚合 BUTTON 节点的 permission_code
 * - matchPerm      镜像后端 has_perm 语义（*:* 全权 / 精确 / 通配前缀）
 * - routePathOf    后端菜单 path → 前端路由 path（映射表，未映射不出导航）
 * - toNavGroups    把菜单树压成侧栏可渲染分组
 *
 * 全部为纯函数，可单测（见 menu.test.js）。
 */

/** 后端语义 icon 串 → emoji（与 Layout 原静态导航保持一致观感） */
export const ICON_MAP = {
  setting: '⚙️', menu: '🧭', role: '👥', region: '🗺️', cloud: '☁️', ops: '🧩',
  school: '🏫', book: '📚', chart: '📈', search: '🔍', wrong: '📕', exam: '📝',
  grade: '✅', report: '💌', shield: '🛡️', target: '🎯',
}

/**
 * 后端菜单 path → 前端路由 path 映射表。
 *
 * 后端 /ops/*、/system/* 是管理面路径，前端路由扁平化（/knowledge 等），
 * 两边历史上各自演进，故以本表桥接；值为 null 表示后端有菜单但前端
 * 尚无对应页面（如 /system/menu 属 T-AM3 前端，未建）——不出现在导航，
 * 避免死链。新增前端页面时在此补一行映射即可。
 */
export const MENU_ROUTE_MAP = {
  '/ops/tenant': null, // 学校管理页（T-AM0 前端）未建
  '/ops/knowledge': '/knowledge',
  '/ops/analytics': '/analytics',
  '/ops/error-distribution': '/error-distribution',
  '/ops/wrong': '/wrong-questions',
  '/ops/exam': '/exam',
  '/ops/grading': '/grading',
  '/ops/reports': '/reports',
  '/ops/compliance': '/compliance',
  '/ops/practice': '/practice',
  '/system/menu': null, // 菜单管理页（T-AM3 前端）未建
  '/system/role': null, // 角色管理页未建
  '/system/region': null, // 区域管理页未建
  '/system/vendor': null, // MCP 厂商管理页（T-AM6）未建
}

/** 菜单 → 可导航的前端路由；非 MENU 类型 / 未映射 / 显式 null 均返回 null */
export function routePathOf(menu) {
  if (!menu || menu.menu_type !== 'MENU' || !menu.path) return null
  if (!(menu.path in MENU_ROUTE_MAP)) return null
  return MENU_ROUTE_MAP[menu.path] || null
}

/** 聚合 BUTTON 节点的 permission_code（目录/菜单无码，过滤掉） */
export function collectPerms(menus) {
  return (menus || [])
    .filter((m) => m.menu_type === 'BUTTON' && m.permission_code)
    .map((m) => m.permission_code)
}

/**
 * 权限匹配，镜像后端 permission.py::has_perm：
 *   1) "*:*" 全权放行
 *   2) 精确命中
 *   3) 通配前缀（perms 含 "system:menu:*" 可放行 "system:menu:add"）
 */
export function matchPerm(perms, required) {
  if (!required) return true
  if (!perms || perms.length === 0) return false
  if (perms.includes('*:*')) return true
  if (perms.includes(required)) return true
  const prefix = required.split(':').slice(0, -1).join(':')
  return perms.includes(`${prefix}:*`)
}

/** 扁平列表 → 树（DIR 为分组，MENU 为叶子；BUTTON 不进导航）；按 sort 排序 */
export function buildMenuTree(flat) {
  const nodes = (flat || []).filter(
    (m) => m.menu_type === 'DIR' || m.menu_type === 'MENU'
  )
  const byId = new Map()
  for (const m of nodes) byId.set(m.menu_id, { ...m, children: [] })
  const roots = []
  for (const m of nodes) {
    const node = byId.get(m.menu_id)
    if (m.parent_id && byId.has(m.parent_id)) {
      byId.get(m.parent_id).children.push(node)
    } else {
      roots.push(node)
    }
  }
  const sortRec = (list) => {
    list.sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0))
    for (const n of list) sortRec(n.children)
  }
  sortRec(roots)
  return roots
}

/**
 * 菜单树 → 侧栏分组。DIR 成为分组标题；MENU 仅保留有前端路由映射的项；
 * 无有效项的分组整体剔除（如「系统设置」在前端页面就绪前不出现）。
 */
export function toNavGroups(tree) {
  const groups = []
  const walk = (nodes, group) => {
    for (const n of nodes) {
      if (n.menu_type === 'DIR') {
        const g = {
          key: n.menu_id,
          label: n.menu_name,
          icon: ICON_MAP[n.icon] || '🧩',
          items: [],
        }
        groups.push(g)
        walk(n.children, g)
      } else {
        const to = routePathOf(n)
        if (to && group) {
          group.items.push({
            to,
            label: n.menu_name,
            icon: ICON_MAP[n.icon] || '📄',
          })
        }
      }
    }
  }
  walk(tree, null)
  return groups.filter((g) => g.items.length > 0)
}
