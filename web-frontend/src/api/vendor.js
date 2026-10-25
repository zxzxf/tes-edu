import client from './client'

// T-AM6 MCP 厂商与数据服务实例 API 层（后端 app/api/vendor_api.py，挂 /api/admin 前缀）。
// 安全口径（前后端一致）：
// - 凭证只写不回显：credential / connection 是入参明文，加密（AES-256-GCM）后落
//   credential_enc / connection_enc；响应只有 has_credential / has_connection 布尔；
// - 编辑时不传凭证即保持已存（传空串同样不覆盖），不存在"清空凭证"路径；
// - 实例端点 vendor_id 路径隔离：跨厂商读写返回 404；
// - 删除守卫：厂商名下有实例时 DELETE /vendor 返回 400。
// client 响应拦截器已解包一层（res => res.data）。

// ---------- 厂商 ----------
export function listVendors() {
  return client.get('/vendor')
}
export function getVendor(vendorId) {
  return client.get(`/vendor/${encodeURIComponent(vendorId)}`)
}
export function createVendor(data) {
  return client.post('/vendor', data)
}
export function updateVendor(vendorId, data) {
  return client.put(`/vendor/${encodeURIComponent(vendorId)}`, data)
}
export function deleteVendor(vendorId) {
  return client.delete(`/vendor/${encodeURIComponent(vendorId)}`)
}

// ---------- 每厂商数据服务实例 ----------
export function listInstances(vendorId) {
  return client.get(`/vendor/${encodeURIComponent(vendorId)}/instances`)
}
export function createInstance(vendorId, data) {
  return client.post(`/vendor/${encodeURIComponent(vendorId)}/instances`, data)
}
export function updateInstance(vendorId, instanceId, data) {
  return client.put(
    `/vendor/${encodeURIComponent(vendorId)}/instances/${encodeURIComponent(instanceId)}`,
    data,
  )
}
export function deleteInstance(vendorId, instanceId) {
  return client.delete(
    `/vendor/${encodeURIComponent(vendorId)}/instances/${encodeURIComponent(instanceId)}`,
  )
}

// ---------- 连通性测试 ----------
// 后端解密凭证后实调 base_url；返回 { ok, status_code, latency_ms, detail }，
// 网络错误也是 200 + ok=false（不 500）。缺凭证/缺 base_url → 400。
export function testVendor(vendorId) {
  return client.post(`/vendor/${encodeURIComponent(vendorId)}/test`)
}
