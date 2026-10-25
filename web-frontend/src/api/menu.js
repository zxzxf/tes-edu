import client from './client'

// T-AM2/T-AM7：按当前用户授权返回可见菜单（扁平列表，含 BUTTON 节点与
// permission_code）。请求经 /api/admin 前缀 → Go 网关（rbac 模式）→ web-backend
// GET /menu/my。前端按 parent_id 自建树（见 utils/menu.js buildMenuTree）。
export function getMyMenus() {
  return client.get('/menu/my')
}
