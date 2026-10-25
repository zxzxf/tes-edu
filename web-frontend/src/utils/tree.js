/**
 * 管理面树形纯函数（T-AM3/T-AM4 前端）。
 *
 * 与 utils/menu.js（导航向，过滤 BUTTON）不同，本模块保留全部节点类型：
 * 菜单管理页要逐行展示 BUTTON，角色授权树要把按钮权限码一并勾选。
 *
 * 全部为纯函数，可单测（见 tree.test.js）：
 * - buildTree        扁平列表 → 树（DIR/MENU/BUTTON 全保留，按 sort 排序）
 * - flattenTree      DFS 扁平化并附 depth（表格/授权列表缩进渲染用）
 * - subtreeIds       节点自身 + 全部后代 ID
 * - findNode         按 menu_id 查找节点
 * - checkState       勾选态三态推导（全选/部分/未选）
 * - toggleSubtree    勾选联动整棵子树（返回新 Set，不改入参）
 * - withAncestors    提交前补齐祖先（/menu/my 按直接关联取数，
 *                    MENU 的父 DIR 未授权会被挂到导航根级，故必须补齐）
 */

/** 扁平列表 → 树（保留全部节点类型；parent 缺失按根挂载；按 sort 升序） */
export function buildTree(flat) {
  const nodes = flat || []
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

/** 深度优先扁平化（附 depth；保留 children 引用，供 checkState 用） */
export function flattenTree(tree) {
  const rows = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      rows.push({ ...n, depth })
      walk(n.children, depth + 1)
    }
  }
  walk(tree, 0)
  return rows
}

/** 节点自身 + 全部后代的 menu_id */
export function subtreeIds(node) {
  const ids = [node.menu_id]
  for (const c of node.children || []) ids.push(...subtreeIds(c))
  return ids
}

/** 按 menu_id 查找节点（找不到返回 null） */
export function findNode(tree, menuId) {
  for (const n of tree || []) {
    if (n.menu_id === menuId) return n
    const hit = findNode(n.children, menuId)
    if (hit) return hit
  }
  return null
}

/**
 * 勾选态推导（角色授权树三态复选框）：
 * 子树全在 checked → 'checked'；部分在 → 'indeterminate'；都不在 → 'unchecked'
 */
export function checkState(node, checked) {
  const ids = subtreeIds(node)
  const hit = ids.filter((id) => checked.has(id)).length
  if (hit === 0) return 'unchecked'
  if (hit === ids.length) return 'checked'
  return 'indeterminate'
}

/**
 * 勾选/取消某节点：联动整棵子树（父节点操作全部子孙；叶子只动自己）。
 * 返回新 Set（不改入参——Vue 响应式下替换引用最稳）。
 * 节点不存在时原样返回副本。
 */
export function toggleSubtree(tree, menuId, checked) {
  const next = new Set(checked)
  const node = findNode(tree, menuId)
  if (!node) return next
  const wantChecked = checkState(node, checked) !== 'checked'
  for (const id of subtreeIds(node)) {
    if (wantChecked) next.add(id)
    else next.delete(id)
  }
  return next
}

/**
 * 提交前的授权集合补齐：勾选项 + 其全部祖先（沿 parent_id 上行）。
 * 带访问集合防环（脏数据 parent 成环时不会死循环）。
 */
export function withAncestors(flat, ids) {
  const byId = new Map((flat || []).map((m) => [m.menu_id, m]))
  const out = new Set()
  for (const id of ids || []) {
    let cur = byId.get(id)
    while (cur && !out.has(cur.menu_id)) {
      out.add(cur.menu_id)
      cur = cur.parent_id ? byId.get(cur.parent_id) : null
    }
  }
  return out
}
