import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Analytics from './Analytics.vue'

vi.mock('../api/admin', () => ({ getAnalytics: vi.fn() }))
import { getAnalytics } from '../api/admin'

const DATA = {
  student_id: 'student_001',
  weak_points: ['二次函数', '被动语态'],
  mastery: 0.72,
  trend: [0.55, 0.6, 0.72]
}

function queryButton(wrapper) {
  return wrapper.findAll('button').find((b) => b.text() === '查询')
}

beforeEach(() => {
  vi.clearAllMocks()
  getAnalytics.mockResolvedValue(DATA)
})

describe('Analytics 学情看板页', () => {
  it('查询后展示掌握度与薄弱点', async () => {
    const wrapper = mount(Analytics)
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(getAnalytics).toHaveBeenCalledWith('student_001')
    expect(wrapper.text()).toContain('72%')
    expect(wrapper.text()).toContain('二次函数')
  })

  it('403 必须如实提示，不能伪装成空态', async () => {
    getAnalytics.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } }
    })
    const wrapper = mount(Analytics)
    await queryButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('权限不足（403）')
    expect(wrapper.text()).not.toContain('输入学生 ID 查询')
  })
})
