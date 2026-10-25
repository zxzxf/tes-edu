import { describe, it, expect } from 'vitest'
import {
  buildTree, flattenTree, subtreeIds, findNode, checkState, toggleSubtree, withAncestors,
} from './tree'

// 与 rbac_seed.MENUS 同构的样本（扁平列表，含 DIR/MENU/BUTTON 三类）
const flat = [
  { menu_id: 'm_sys', parent_id: null, menu_name: '系统设置', menu_type: 'DIR', path: '/system', sort: 1 },
  { menu_id: 'm_sys_menu', parent_id: 'm_sys', menu_name: '菜单管理', menu_type: 'MENU', path: '/system/menu', sort: 1 },
  { menu_id: 'm_sys_menu_add', parent_id: 'm_sys_menu', menu_name: '新建菜单', menu_type: 'BUTTON', path: '', sort: 1, permission_code: 'system:menu:add' },
  { menu_id: 'm_sys_menu_edit', parent_id: 'm_sys_menu', menu_name: '编辑菜单', menu_type: 'BUTTON', path: '', sort: 2, permission_code: 'system:menu:edit' },
  { menu_id: 'm_sys_role', parent_id: 'm_sys', menu_name: '角色管理', menu_type: 'MENU', path: '/system/role', sort: 2 },
  { menu_id: 'm_ops', parent_id: null, menu_name: '区域运营', menu_type: 'DIR', path: '/ops', sort: 2 },
  { menu_id: 'm_ops_analytics', parent_id: 'm_ops', menu_name: '学情看板', menu_type: 'MENU', path: '/ops/analytics', sort: 3 },
]

describe('buildTree（管理面向，保留 BUTTON）', () => {
  it('按 parent_id 建树，BUTTON 作为 MENU 子节点保留', () => {
    const tree = buildTree(flat)
    expect(tree.map((n) => n.menu_id)).toEqual(['m_sys', 'm_ops'])
    const menu = tree[0].children.find((c) => c.menu_id === 'm_sys_menu')
    expect(menu.children.map((c) => c.menu_id)).toEqual(['m_sys_menu_add', 'm_sys_menu_edit'])
  })

  it('按 sort 升序（根与子层都排）', () => {
    const tree = buildTree(flat)
    expect(tree[0].children.map((c) => c.menu_id)).toEqual(['m_sys_menu', 'm_sys_role'])
  })

  it('父节点缺失按根挂载（不丢节点）', () => {
    const tree = buildTree([{ menu_id: 'x', parent_id: 'gone', menu_type: 'MENU', sort: 1 }])
    expect(tree.map((n) => n.menu_id)).toEqual(['x'])
  })

  it('空列表安全', () => {
    expect(buildTree([])).toEqual([])
    expect(buildTree(null)).toEqual([])
  })
})

describe('flattenTree', () => {
  it('DFS 附带 depth（供缩进渲染）', () => {
    const rows = flattenTree(buildTree(flat))
    expect(rows.map((r) => [r.menu_id, r.depth])).toEqual([
      ['m_sys', 0], ['m_sys_menu', 1], ['m_sys_menu_add', 2], ['m_sys_menu_edit', 2],
      ['m_sys_role', 1], ['m_ops', 0], ['m_ops_analytics', 1],
    ])
  })
})

describe('subtreeIds / findNode', () => {
  it('subtreeIds 含自身与全部后代', () => {
    const tree = buildTree(flat)
    expect(subtreeIds(findNode(tree, 'm_sys_menu'))).toEqual(['m_sys_menu', 'm_sys_menu_add', 'm_sys_menu_edit'])
    expect(subtreeIds(findNode(tree, 'm_ops'))).toEqual(['m_ops', 'm_ops_analytics'])
  })

  it('findNode 穿透多层；找不到返回 null', () => {
    const tree = buildTree(flat)
    expect(findNode(tree, 'm_sys_menu_edit').menu_name).toBe('编辑菜单')
    expect(findNode(tree, 'nope')).toBe(null)
  })
})

describe('checkState（三态推导）', () => {
  const tree = buildTree(flat)

  it('子树全选 → checked', () => {
    const checked = new Set(['m_sys_menu', 'm_sys_menu_add', 'm_sys_menu_edit'])
    expect(checkState(findNode(tree, 'm_sys_menu'), checked)).toBe('checked')
  })

  it('部分勾选 → indeterminate', () => {
    const checked = new Set(['m_sys_menu_add'])
    expect(checkState(findNode(tree, 'm_sys_menu'), checked)).toBe('indeterminate')
    expect(checkState(findNode(tree, 'm_sys'), checked)).toBe('indeterminate')
  })

  it('全未选 → unchecked', () => {
    expect(checkState(findNode(tree, 'm_sys'), new Set())).toBe('unchecked')
  })
})

describe('toggleSubtree（父节点联动子孙）', () => {
  const tree = buildTree(flat)

  it('勾选父节点 → 整棵子树全部选中', () => {
    const next = toggleSubtree(tree, 'm_sys_menu', new Set())
    expect(next.has('m_sys_menu')).toBe(true)
    expect(next.has('m_sys_menu_add')).toBe(true)
    expect(next.has('m_sys_menu_edit')).toBe(true)
  })

  it('再勾同一父节点 → 整棵子树取消', () => {
    let checked = toggleSubtree(tree, 'm_sys_menu', new Set())
    checked = toggleSubtree(tree, 'm_sys_menu', checked)
    expect(checked.size).toBe(0)
  })

  it('叶子节点只动自己，不影响父级显式集合', () => {
    const checked = toggleSubtree(tree, 'm_sys_menu_add', new Set())
    expect([...checked]).toEqual(['m_sys_menu_add'])
  })

  it('不改入参（返回新 Set）', () => {
    const origin = new Set()
    const next = toggleSubtree(tree, 'm_ops', origin)
    expect(origin.size).toBe(0)
    expect(next).not.toBe(origin)
  })

  it('节点不存在时返回原集合副本', () => {
    const origin = new Set(['m_ops'])
    expect([...toggleSubtree(tree, 'nope', origin)]).toEqual(['m_ops'])
  })
})

describe('withAncestors（提交前补齐祖先链）', () => {
  it('勾选叶子时自动带上父 DIR', () => {
    const ids = withAncestors(flat, ['m_ops_analytics'])
    expect([...ids].sort()).toEqual(['m_ops', 'm_ops_analytics'])
  })

  it('多级祖先全部补齐', () => {
    const ids = withAncestors(flat, ['m_sys_menu_add'])
    expect([...ids].sort()).toEqual(['m_sys', 'm_sys_menu', 'm_sys_menu_add'])
  })

  it('已选父级不重复；空集合安全', () => {
    const ids = withAncestors(flat, ['m_sys', 'm_sys_menu_add'])
    expect([...ids].sort()).toEqual(['m_sys', 'm_sys_menu', 'm_sys_menu_add'])
    expect([...withAncestors(flat, [])]).toEqual([])
  })

  it('parent 成环不死循环（脏数据防护）', () => {
    const cyclic = [
      { menu_id: 'a', parent_id: 'b', menu_type: 'MENU', sort: 1 },
      { menu_id: 'b', parent_id: 'a', menu_type: 'DIR', sort: 1 },
    ]
    expect([...withAncestors(cyclic, ['a'])].sort()).toEqual(['a', 'b'])
  })
})
