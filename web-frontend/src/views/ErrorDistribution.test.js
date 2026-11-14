import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ErrorDistribution from './ErrorDistribution.vue'

vi.mock('../api/admin', () => ({
  getErrorDistribution: vi.fn(),
  downloadErrorDistributionCsv: vi.fn()
}))

import { getErrorDistribution, downloadErrorDistributionCsv } from '../api/admin'

const DATA = {
  total: 38,
  group_by: 'error_type',
  scope: {
    tenant_id: 'school_pilot_01', subject: null, period_days: 90,
    from: '2026-08-16T00:00:00+00:00', to: '2026-11-14T00:00:00+00:00',
    student_count: 2
  },
  rows: [
    { key: 'computation', label: '计算失误', count: 20, ratio: 0.526,
      student_count: 2, advice: '建议加强分步校验' }
  ],
  attribution: { deterministic_ratio: 0.8, unknown_ratio: 0.2 }
}

function mountPage() { return mount(ErrorDistribution) }
function queryButton(wrapper) {
  return wrapper.findAll('button').find((b) => b.text() === '查询')
}

beforeEach(() => {
  vi.clearAllMocks()
  getErrorDistribution.mockResolvedValue(DATA)
  downloadErrorDistributionCsv.mockResolvedValue(undefined)
})

describe('ErrorDistribution 班级错因分布页（A 方案）', () => {
  it('查询默认按 90 天窗口，展示租户范围与错因数据', async () => {
    const wrapper = mountPage()
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(getErrorDistribution).toHaveBeenCalledWith(
      expect.objectContaining({ group_by: 'error_type', period_days: 90 })
    )
    expect(wrapper.text()).toContain('38')
    expect(wrapper.text()).toContain('计算失误')
    expect(wrapper.text()).toContain('school_pilot_01')
  })

  it('403 必须如实提示加载失败，不能伪装成没数据', async () => {
    getErrorDistribution.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } }
    })
    const wrapper = mountPage()
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('权限不足（403）')
    expect(wrapper.text()).not.toContain('填好条件点')
    expect(wrapper.find('.err').exists()).toBe(true)
  })

  it('500 也要报错，且清掉上一次的结果', async () => {
    const wrapper = mountPage()
    await queryButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('计算失误')

    getErrorDistribution.mockRejectedValueOnce({ response: { status: 500, data: {} } })
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('（500）')
    expect(wrapper.text()).not.toContain('计算失误')
  })

  it('窗口内无数据时展示后端给出的放宽周期提示', async () => {
    getErrorDistribution.mockResolvedValueOnce({
      ...DATA, total: 0, rows: [],
      scope: { ...DATA.scope, hint: '本周期（30 天）内没有错因记录；把周期调到 120 天以上即可查看。' }
    })
    const wrapper = mountPage()
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.find('.hintbox').text()).toContain('周期调到 120 天以上')
  })

  it('导出失败也要提示，不产生未处理的 Promise rejection', async () => {
    downloadErrorDistributionCsv.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } }
    })
    const wrapper = mountPage()
    await queryButton(wrapper).trigger('click')
    await flushPromises()
    const csvBtn = wrapper.findAll('button').find((b) => b.text() === '导出 CSV')
    await csvBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('导出失败')
    expect(wrapper.text()).toContain('权限不足（403）')
  })
})
