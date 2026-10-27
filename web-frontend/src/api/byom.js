import client from './client'

// W6 BYOM 学校配置大模型 API 层（后端 app/api/byom.py，挂 /api/admin 前缀）。
// client 响应拦截器已解包一层（res => res.data）。
//
// 安全口径（前后端一致）：
// - api_key / embed_api_key 是明文入参，加密（AES-256-GCM）后落库；列表/详情响应
//   仅回 api_key_masked（末 4 位）与 has_api_key 布尔；
// - 明文只在用户显式点「反显」时经 POST /byom/config/reveal 单条取回
//   （需 system:byom:manage，后端留审计日志）；
// - 编辑时 api_key 留空 = 不覆盖已存凭证；
// - 连通性测试用候选字段实调端点，返回 { ok, status_code, latency_ms, detail }。

// 预设供应商清单（下拉自动填充 base_url / 候选模型）
export function listPresets() {
  return client.get('/byom/presets')
}

// 模型配置列表（超管看全部租户，学校只看本租户）
export function listConfigs() {
  return client.get('/byom/configs')
}

// 当前租户模型配置（脱敏）
export function getConfig() {
  return client.get('/byom/config')
}

// 明文回显密钥（需 system:byom:manage；后端记录审计日志）
export function revealKey(tenantId) {
  return client.post('/byom/config/reveal', tenantId ? { tenant_id: tenantId } : {})
}

// 用库里已存配置做连通测试（列表行内按钮，无需重新粘贴 key）
export function testStoredConfig(tenantId) {
  return client.post(`/byom/configs/${encodeURIComponent(tenantId)}/test`)
}

// 保存 / 更新（api_key 空 = 不覆盖）
export function saveConfig(data) {
  return client.put('/byom/config', data)
}

// 删除配置（回落全局 env）。tenantId 仅超管可传（删指定租户那一行）
export function deleteConfig(tenantId) {
  const cfg = tenantId ? { params: { tenant_id: tenantId } } : {}
  return client.delete('/byom/config', cfg)
}

// 连通性自检（用候选配置实调端点）
export function testConfig(data) {
  return client.post('/byom/connectivity-test', data)
}
