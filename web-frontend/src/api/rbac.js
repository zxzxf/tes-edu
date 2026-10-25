import client from './client'

// T-AM3/T-AM4 前端管理页 API 层（后端 app/api/rbac_api.py，挂 /api/admin 前缀）。
// - 菜单 CRUD 的写操作走后端 require_perm 按钮码
//   （system:menu:add / edit / delete），越权由后端 403 兜底；
// - 角色授权 PUT 是全量替换语义——提交前必须先 getRoleMenus 回显当前授权，
//   否则空手提交会把角色菜单清空（后端无合并语义）。
// client 响应拦截器已解包一层（res => res.data），204 响应的 data 为空串。

// ---------- T-AM3 菜单管理 ----------
export function listMenus() {
  return client.get('/menu')
}
export function createMenu(data) {
  return client.post('/menu', data)
}
export function updateMenu(menuId, data) {
  return client.put(`/menu/${encodeURIComponent(menuId)}`, data)
}
export function deleteMenu(menuId) {
  return client.delete(`/menu/${encodeURIComponent(menuId)}`)
}

// ---------- T-AM4 角色管理 + 菜单授权 ----------
export function listRoles() {
  return client.get('/role')
}
export function getRoleMenus(roleId) {
  return client.get(`/role/${encodeURIComponent(roleId)}/menus`)
}
export function updateRoleMenus(roleId, menuIds) {
  return client.put(`/role/${encodeURIComponent(roleId)}/menus`, { menu_ids: menuIds })
}
