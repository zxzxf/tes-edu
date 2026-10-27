import client from './client'

// T-AM3/T-AM4/T-AM5 前端管理页 API 层（后端 app/api/rbac_api.py，挂 /api/admin 前缀）。
// - 菜单 CRUD 的写操作走后端 require_perm 按钮码
//   （system:menu:add / edit / delete），越权由后端 403 兜底；
// - 角色授权 PUT 是全量替换语义——提交前必须先 getRoleMenus 回显当前授权，
//   否则空手提交会把角色菜单清空（后端无合并语义）；
// - 区域写操作走 system:region:add / edit / delete 三按钮码；
//   DELETE 有守卫：仍有学校/用户/子区域绑定时 400。
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

// ---------- T-AM0 租户/学校管理 ----------
// 后端 app/api/rbac_api.py::/tenant。TenantOut 见后端定义（租户/学校）。
// T-AM9 用户管理页用其填充「租户」下拉。
export function listTenants() {
  return client.get('/tenant')
}
export function createTenant(data) {
  return client.post('/tenant', data)
}
export function updateTenant(tenantId, data) {
  return client.put(`/tenant/${encodeURIComponent(tenantId)}`, data)
}
export function deleteTenant(tenantId) {
  return client.delete(`/tenant/${encodeURIComponent(tenantId)}`)
}

// ---------- T-AM5 区域管理 ----------
// 后端 app/api/rbac_api.py::/region。RegionOut 带 tenant_count/user_count
// （绑定的学校/用户数，同时是删除守卫依据：有绑定时 DELETE 返回 400，
// detail 形如 "region in use: 1 tenants, 0 users, 0 sub-regions"）。
export function listRegions() {
  return client.get('/region')
}
export function createRegion(data) {
  return client.post('/region', data)
}
export function updateRegion(regionId, data) {
  return client.put(`/region/${encodeURIComponent(regionId)}`, data)
}
export function deleteRegion(regionId) {
  return client.delete(`/region/${encodeURIComponent(regionId)}`)
}

// ---------- T-AM9 用户管理 ----------
// 后端 app/api/rbac_api.py::/user。仅 admin 可见（系统设置内），写操作走
// system:user:{add,edit,delete,resetpwd} 按钮码，越权由后端 403 兜底。
// reset-password 成功响应含明文 password（仅本次返回，不落库），前端需显式告知用户。
export function listUsers(params = {}) {
  return client.get('/user', { params })
}
export function createUser(data) {
  return client.post('/user', data)
}
export function updateUser(userId, data) {
  return client.put(`/user/${encodeURIComponent(userId)}`, data)
}
export function deleteUser(userId) {
  return client.delete(`/user/${encodeURIComponent(userId)}`)
}
export function resetUserPassword(userId, password) {
  return client.post(`/user/${encodeURIComponent(userId)}/reset-password`, { password })
}
