import { describe, it, expect, vi, beforeEach } from 'vitest'
import client from './client'
import {
  reviewKpNode,
  upsertKpNodes,
  listKpNodes,
  createCrawlJob,
  rollbackCrawlRun,
  getCrawlMetrics
} from './admin'

// 锁定 B 端采集/审核页的 HTTP 契约（URL / 方法 / 载荷），与
// web-backend/app/api/course.py 逐条对齐；2026-11-11 联调新增。
vi.mock('./client', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() }
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('M5 课程采集 API 契约', () => {
  it('reviewKpNode 对中文 code 做 URL 编码后再拼路径', async () => {
    client.post.mockResolvedValue({ status: 'published' })
    await reviewKpNode('第一章 数列_4ef3ec', 'published')
    expect(client.post).toHaveBeenCalledWith(
      `/course/kp-nodes/${encodeURIComponent('第一章 数列_4ef3ec')}/review`,
      { status: 'published' }
    )
  })

  it('upsertKpNodes 把候选数组原样 POST（后端 KpUpsert 列表）', async () => {
    const items = [{ code: 'C1', name: '数列', parent_code: null, subject: '数学', source: 'crawled' }]
    client.post.mockResolvedValue({ created: 1, skipped: 0, merged: 0, codes: ['C1'] })
    const out = await upsertKpNodes(items)
    expect(client.post).toHaveBeenCalledWith('/course/kp-nodes', items)
    expect(out.created).toBe(1)
  })

  it('listKpNodes 带 status 查询；空参不传 params', async () => {
    client.get.mockResolvedValue([])
    await listKpNodes('draft')
    expect(client.get).toHaveBeenCalledWith('/course/kp-nodes', { params: { status: 'draft' } })
    await listKpNodes()
    expect(client.get).toHaveBeenLastCalledWith('/course/kp-nodes', { params: {} })
  })

  it('createCrawlJob 透传 schedule=null（空 cron 不被判非法）', async () => {
    client.post.mockResolvedValue({ id: 9 })
    const body = { name: '每月采集', source_id: null, path: 'P2', schedule: null, budget_limit: 100 }
    await createCrawlJob(body)
    expect(client.post).toHaveBeenCalledWith('/course/jobs', body)
  })

  it('rollbackCrawlRun / getCrawlMetrics 路径与后端一致', async () => {
    client.post.mockResolvedValue({ removed_questions: 2, removed_papers: 1 })
    await rollbackCrawlRun(7)
    expect(client.post).toHaveBeenCalledWith('/course/runs/7/rollback')
    client.get.mockResolvedValue({ runs: 1 })
    await getCrawlMetrics()
    expect(client.get).toHaveBeenCalledWith('/course/metrics')
  })
})
