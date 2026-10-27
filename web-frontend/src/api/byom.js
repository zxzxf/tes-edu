import client from './client'

// W6 BYOM 学校配置大模型 API 层（后端 app/api/byom.py，挂 /api/admin 前缀）。
// client 响应拦截器已解包一层（res => res.data）。
//
// 安全口径（前后端一致）：
// - api_key / embed_api_key 是明文入参，加密（AES-256-GCM）后落库；响应仅回
//   api_key_masked（末 4 位）与 has_api_key 布尔，明文任何路径都不回显；
// - 编辑时 api_key 留空 = 不覆盖已存凭证；
// - 连通性测试用候选字段实调端点，返回 { ok, status_code, latency_ms, detail }。

// 预设供应商清单（下拉自动填充 base_url / 候选模型）
export function listPresets() {
  return client.get('/byom/presets')
}

// 当前租户模型配置（脱敏）
export function getConfig() {
  return client.get('/byom/config')
}

// 保存 / 更新（api_key 空 = 不覆盖）
export function saveConfig(data) {
  return client.put('/byom/config', data)
}

// 删除配置（回落全局 env）
export function deleteConfig() {
  return client.delete('/byom/config')
}

// 连通性自检（用候选配置实调端点）
export function testConfig(data) {
  return client.post('/byom/connectivity-test', data)
}
