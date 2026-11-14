import client from './client'

// T-DICT 数据字典 API（后端 app/api/dict_api.py，挂 /api/admin 前缀）。
// client 响应拦截器已解包一层（res => res.data）。
// - 列表 / 增删改仅 admin 可调用（后端 require_role(ADMIN)）；
// - /dict/map 任意已登录角色可读（用户管理列表 school 也用它翻译角色名）。

// 列出全部字典节点（扁平，前端按 parent_id 建树）
export function listDict() {
  return client.get('/dict')
}

// 新建节点
export function createDict(data) {
  return client.post('/dict', data)
}

// 更新节点
export function updateDict(dictId, data) {
  return client.put(`/dict/${encodeURIComponent(dictId)}`, data)
}

// 删除节点（有子节点时后端 400 拒绝）
export function deleteDict(dictId) {
  return client.delete(`/dict/${encodeURIComponent(dictId)}`)
}

// 取某分类下 {dict_code: dict_name} 翻译映射
export function dictMap(type) {
  return client.get('/dict/map', { params: { type } })
}
