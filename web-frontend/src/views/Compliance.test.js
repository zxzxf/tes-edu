import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Compliance from './Compliance.vue'

vi.mock('../api/admin', () => ({
  listCompliance: vi.fn(),
  reviewCompliance: vi.fn(),
  createCompliance: vi.fn()
}))

import { listCompliance, reviewCompliance } from '../api/admin'

const PENDING = [
  {
    id: 7, tenant_id: 'school_pilot_01', type: '题目', status: 'pending',
    content: '下列各组对象中，能组成集合的是（  ）', source: 'crawl',
    ref_type: 'question', ref_id: 259, created_at: '2026-11-11T09:52:10+00:00'
  }
]

function mountPage() {
  return mount(Compliance)
}

beforeEach(() => {
  vi.clearAllMocks()
  listCompliance.mockResolvedValue(PENDING)
  reviewCompliance.mockResolvedValue({ ok: true })
})

describe('Compliance 合规审核页（0022 真实生产者）', () => {
  it('挂载即加载 pending，展示题干与来源', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listCompliance).toHaveBeenCalledWith('pending')
    expect(wrapper.text()).toContain('能组成集合')
    expect(wrapper.text()).toContain('采集入库')
    expect(wrapper.text()).toContain('共 1 条')
  })

  it('403 必须如实提示加载失败，不能伪装成「暂无待审核内容」', async () => {
    listCompliance.mockRejectedValueOnce({ response: { status: 403, data: { detail: 'insufficient role' } } })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('权限不足（403）')
    expect(wrapper.text()).not.toContain('该状态下暂无审核内容')
  })

  it('点「通过」调 review(approved) 并重新拉列表', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const approve = buttons.find((b) => b.text() === '通过')
    await approve.trigger('click')
    await flushPromises()

    expect(reviewCompliance).toHaveBeenCalledWith(7, 'approved')
    expect(listCompliance).toHaveBeenCalledTimes(2)
  })

  it('切换到「已驳回」会带 status=rejected 重新查询', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('select').setValue('rejected')
    await flushPromises()

    expect(listCompliance).toHaveBeenLastCalledWith('rejected')
  })
})
