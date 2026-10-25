import { describe, it, expect } from 'vitest'
import {
  buildMenuTree, collectPerms, matchPerm, routePathOf, toNavGroups, MENU_ROUTE_MAP,
} from './menu'

// 与 rbac_seed.MENUS 同构的样本（扁平列表 + BUTTON 节点）
const flat = [
  { menu_id: 'm_sys', parent_id: null, menu_name: '系统设置', menu_type: 'DIR', path: '/system', sort: 1 },
  { menu_id: 'm_sys_menu', parent_id: 'm_sys', menu_name: '菜单管理', menu_type: 'MENU', path: '/system/menu', sort: 1 },
  { menu_id: 'm_sys_menu_add', parent_id: 'm_sys_menu', menu_name: '新建菜单', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'system:menu:add' },
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
  { menu_id: 'm_ops_analytics', parent_id: 'm_ops', menu_name: '学情看板', menu_type: 'MENU', path: '/ops/analytics', sort: 3 },
  { menu_id: 'm_ops_exam', parent_id: 'm_ops', menu_name: '自动组卷', menu_type: 'MENU', path: '/ops/exam', sort: 5 },
  { menu_id: 'm_ops_exam_gen', parent_id: 'm_ops_exam', menu_name: '生成组卷', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'exam:generate' },
  { menu_id: 'm_ops_reports', parent_id: 'm_ops', menu_name: '家长周报', menu_type: 'MENU', path: '/ops/reports', sort: 7 },
]

describe('buildMenuTree', () => {
  it('按 parent_id 建树，BUTTON 不进导航', () => {
    const tree = buildMenuTree(flat)
    expect(tree.map((n) => n.menu_id)).toEqual(['m_sys', 'm_ops'])
    const ops = tree.find((n) => n.menu_id === 'm_ops')
    // sort 排序：analytics(3) < exam(5) < reports(7)
    expect(ops.children.map((c) => c.menu_id)).toEqual([
      'm_ops_analytics', 'm_ops_exam', 'm_ops_reports',
    ])
    // BUTTON 只作为权限码载体存在于扁平列表，树里没有
    const ids = JSON.stringify(tree)
    expect(ids).not.toContain('m_ops_exam_gen')
    expect(ids).not.toContain('m_sys_menu_add')
  })

  it('父节点缺失时按根挂载（不丢节点）', () => {
    const orphan = [{ menu_id: 'x', parent_id: 'gone', menu_name: '孤儿', menu_type: 'MENU', path: '/ops/analytics', sort: 1 }]
    const tree = buildMenuTree(orphan)
    expect(tree).toHaveLength(1)
    expect(tree[0].menu_id).toBe('x')
  })

  it('空列表返回空树', () => {
    expect(buildMenuTree([])).toEqual([])
    expect(buildMenuTree(null)).toEqual([])
  })
})

describe('collectPerms', () => {
  it('只聚合 BUTTON 节点的 permission_code', () => {
    expect(collectPerms(flat).sort()).toEqual(['exam:generate', 'system:menu:add'])
  })

  it('目录/菜单无码，空列表安全', () => {
    expect(collectPerms([{ menu_type: 'MENU' }])).toEqual([])
    expect(collectPerms(null)).toEqual([])
  })
})

describe('matchPerm（镜像后端 has_perm）', () => {
  it('空权限码恒放行', () => {
    expect(matchPerm([], '')).toBe(true)
    expect(matchPerm([], null)).toBe(true)
  })

  it('*:* 全权放行', () => {
    expect(matchPerm(['*:*'], 'exam:generate')).toBe(true)
  })

  it('精确命中', () => {
    expect(matchPerm(['exam:generate'], 'exam:generate')).toBe(true)
    expect(matchPerm(['exam:generate'], 'grading:submit')).toBe(false)
  })

  it('通配前缀（system:menu:* 覆盖 system:menu:add）', () => {
    expect(matchPerm(['system:menu:*'], 'system:menu:add')).toBe(true)
    expect(matchPerm(['system:menu:*'], 'system:role:auth')).toBe(false)
  })

  it('空权限集合拒绝任何非空码', () => {
    expect(matchPerm([], 'exam:generate')).toBe(false)
  })
})

describe('routePathOf（后端 path → 前端路由映射）', () => {
  it('已映射的 MENU 返回前端路由', () => {
    expect(routePathOf({ menu_type: 'MENU', path: '/ops/knowledge' })).toBe('/knowledge')
    expect(routePathOf({ menu_type: 'MENU', path: '/ops/error-distribution' })).toBe('/error-distribution')
    expect(routePathOf({ menu_type: 'MENU', path: '/ops/wrong' })).toBe('/wrong-questions')
    // T-AM3/T-AM4 前端管理页（2026-10-26 建）后，/system/* 两个管理页可导航
    expect(routePathOf({ menu_type: 'MENU', path: '/system/menu' })).toBe('/system/menu')
    expect(routePathOf({ menu_type: 'MENU', path: '/system/role' })).toBe('/system/role')
  })

  it('前端无页面的菜单（null）不导航', () => {
    expect(routePathOf({ menu_type: 'MENU', path: '/ops/tenant' })).toBe(null)
    expect(routePathOf({ menu_type: 'MENU', path: '/system/region' })).toBe(null)
    expect(routePathOf({ menu_type: 'MENU', path: '/system/vendor' })).toBe(null)
  })

  it('未知 path / 非 MENU / 空 path 一律 null（fail-closed）', () => {
    expect(routePathOf({ menu_type: 'MENU', path: '/ops/unknown' })).toBe(null)
    expect(routePathOf({ menu_type: 'DIR', path: '/ops' })).toBe(null)
    expect(routePathOf({ menu_type: 'BUTTON', path: '' })).toBe(null)
    expect(routePathOf(null)).toBe(null)
  })

  it('映射表覆盖全部 /ops 业务页', () => {
    for (const p of ['/ops/knowledge', '/ops/analytics', '/ops/error-distribution', '/ops/wrong',
      '/ops/exam', '/ops/grading', '/ops/reports', '/ops/compliance', '/ops/practice']) {
      expect(MENU_ROUTE_MAP[p]).toMatch(/^\//)
    }
  })
})

describe('toNavGroups', () => {
  it('DIR 成分组；无前端页面的菜单与空分组被剔除', () => {
    const groups = toNavGroups(buildMenuTree(flat))
    // T-AM3 前端建好后，系统设置组（含 /system/menu 映射）出现在导航中
    expect(groups.map((g) => g.label)).toEqual(['系统设置', '区域运营'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/system/menu'])
    expect(groups[1].items.map((i) => i.to)).toEqual(['/analytics', '/exam', '/reports'])
  })

  it('空树返回空分组', () => {
    expect(toNavGroups([])).toEqual([])
  })
})
