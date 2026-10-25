import { describe, it, expect, beforeEach, vi } from 'vitest'
import client from './client'
import {
  listVendors, getVendor, createVendor, updateVendor, deleteVendor,
  listInstances, createInstance, updateInstance, deleteInstance, testVendor,
} from './vendor'

// mock 掉 axios 实例：锁定「URL + 方法 + 载荷」契约，防止前后端漂移
vi.mock('./client', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('T-AM6 厂商 API（/vendor）', () => {
  it('listVendors → GET /vendor，行带 has_credential/instance_count', async () => {
    client.get.mockResolvedValue([
      { vendor_id: 'v_aliyun', vendor_name: '阿里云', has_credential: true, instance_count: 2 },
    ])
    const rows = await listVendors()
    expect(client.get).toHaveBeenCalledWith('/vendor')
    expect(rows[0].has_credential).toBe(true)
    expect(rows[0].instance_count).toBe(2)
  })

  it('getVendor → GET /vendor/{id}', async () => {
    client.get.mockResolvedValue({ vendor_id: 'v_aliyun', has_credential: true })
    const row = await getVendor('v_aliyun')
    expect(client.get).toHaveBeenCalledWith('/vendor/v_aliyun')
    expect(row.vendor_id).toBe('v_aliyun')
  })

  it('createVendor → POST /vendor 带载荷（含明文 credential）', async () => {
    const body = {
      vendor_id: 'v_aliyun', vendor_name: '阿里云', vendor_type: 'aliyun',
      base_url: 'https://x', auth_type: 'apikey', credential: 'sk-1', status: 1,
    }
    client.post.mockResolvedValue({ ...body, has_credential: true })
    await createVendor(body)
    expect(client.post).toHaveBeenCalledWith('/vendor', body)
  })

  it('updateVendor → PUT /vendor/{id} 带载荷', async () => {
    const body = { vendor_id: 'v_aliyun', vendor_name: '改', vendor_type: 'aliyun', status: 1 }
    client.put.mockResolvedValue({ ...body, has_credential: true })
    await updateVendor('v_aliyun', body)
    expect(client.put).toHaveBeenCalledWith('/vendor/v_aliyun', body)
  })

  it('deleteVendor → DELETE /vendor/{id}', async () => {
    client.delete.mockResolvedValue('')
    await deleteVendor('v_aliyun')
    expect(client.delete).toHaveBeenCalledWith('/vendor/v_aliyun')
  })

  it('vendor_id 含特殊字符时转义（防路径注入）', async () => {
    client.put.mockResolvedValue({})
    await updateVendor('a/b c', { vendor_id: 'a/b c' })
    expect(client.put).toHaveBeenCalledWith('/vendor/a%2Fb%20c', { vendor_id: 'a/b c' })
  })
})

describe('T-AM6 实例 API（/vendor/{id}/instances）', () => {
  it('listInstances → GET /vendor/{id}/instances', async () => {
    client.get.mockResolvedValue([{ instance_id: 'inst_1', vendor_id: 'v_aliyun' }])
    const rows = await listInstances('v_aliyun')
    expect(client.get).toHaveBeenCalledWith('/vendor/v_aliyun/instances')
    expect(rows[0].instance_id).toBe('inst_1')
  })

  it('createInstance → POST /vendor/{id}/instances 带载荷', async () => {
    const body = {
      instance_id: 'inst_1', service_name: '主', data_namespace: 'ns_1',
      isolation_mode: 'logical', connection: 'pg://x', status: 1,
    }
    client.post.mockResolvedValue({ ...body, has_connection: true })
    await createInstance('v_aliyun', body)
    expect(client.post).toHaveBeenCalledWith('/vendor/v_aliyun/instances', body)
  })

  it('updateInstance → PUT /vendor/{id}/instances/{iid} 带载荷', async () => {
    const body = { instance_id: 'inst_1', service_name: '改', data_namespace: 'ns_1' }
    client.put.mockResolvedValue({ ...body })
    await updateInstance('v_aliyun', 'inst_1', body)
    expect(client.put).toHaveBeenCalledWith(
      '/vendor/v_aliyun/instances/inst_1', body,
    )
  })

  it('deleteInstance → DELETE /vendor/{id}/instances/{iid}', async () => {
    client.delete.mockResolvedValue('')
    await deleteInstance('v_aliyun', 'inst_1')
    expect(client.delete).toHaveBeenCalledWith('/vendor/v_aliyun/instances/inst_1')
  })

  it('vendor_id / instance_id 含特殊字符时双层转义', async () => {
    client.put.mockResolvedValue({})
    await updateInstance('a/b', 'c d', { instance_id: 'c d' })
    expect(client.put).toHaveBeenCalledWith(
      '/vendor/a%2Fb/instances/c%20d', { instance_id: 'c d' },
    )
  })
})

describe('T-AM6 连通性测试 API', () => {
  it('testVendor → POST /vendor/{id}/test，返回 { ok, status_code, latency_ms, detail }', async () => {
    client.post.mockResolvedValue({ ok: true, status_code: 200, latency_ms: 120, detail: 'HTTP 200' })
    const res = await testVendor('v_aliyun')
    expect(client.post).toHaveBeenCalledWith('/vendor/v_aliyun/test')
    expect(res.ok).toBe(true)
    expect(res.status_code).toBe(200)
  })
})
