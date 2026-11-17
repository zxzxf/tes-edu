import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Annotation from './Annotation.vue'

vi.mock('../api/admin', () => ({
  listAnnotationSuggestions: vi.fn(),
  reviewAnnotationSuggestion: vi.fn(),
  getAnnotationStats: vi.fn(),
}))

import { listAnnotationSuggestions, reviewAnnotationSuggestion, getAnnotationStats } from '../api/admin'

const STATS = {
  queue: { difficulty: { pending: 2 } },
  course_kb: { published_total: 225, difficulty_gap: 139, year_gap: 14, paper_type_gap: 2 },
}

const PENDING = [
  {
    id: 2, content_hash: 'fbc06c2b12345678', field_name: 'difficulty',
    suggested_value: '2', reason: '含椭圆基础运算，难度宜降一档',
    source_model: 'qwen3.6-flash', prompt_version: 1, status: 'pending',
  },
]

function mountPage() {
  return mount(Annotation)
}

beforeEach(() => {
  vi.clearAllMocks()
  listAnnotationSuggestions.mockResolvedValue(PENDING)
  reviewAnnotationSuggestion.mockResolvedValue({ ok: true })
  getAnnotationStats.mockResolvedValue(STATS)
})

describe('Annotation 题库标注待校页（T-CAP1）', () => {
  it('挂载即加载 pending 与看板，展示缺口数字', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listAnnotationSuggestions).toHaveBeenCalledWith({ status: 'pending' })
    expect(getAnnotationStats).toHaveBeenCalled()
    expect(wrapper.text()).toContain('fbc06c2b12')            // 题目短哈希
    expect(wrapper.text()).toContain('难度')                  // 字段中文
    expect(wrapper.text()).toContain('225')                   // 已发布题
    expect(wrapper.text()).toContain('139')                   // 难度缺口
    expect(wrapper.text()).toContain('共 1 条')
  })

  it('403 必须如实提示加载失败，不能伪装成「暂无建议」', async () => {
    listAnnotationSuggestions.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } },
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('权限不足（403）')
    expect(wrapper.text()).not.toContain('暂无标注建议')
  })

  it('点「通过」调 review(approved)，并刷新列表+看板', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const approve = wrapper.findAll('button').find((b) => b.text() === '通过')
    await approve.trigger('click')
    await flushPromises()

    expect(reviewAnnotationSuggestion).toHaveBeenCalledWith(2, 'approved')
    expect(listAnnotationSuggestions).toHaveBeenCalledTimes(2)
    expect(getAnnotationStats).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('写回题库')
  })

  it('点「驳回」调 review(rejected)，提示题库未改动', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const reject = wrapper.findAll('button').find((b) => b.text() === '驳回')
    await reject.trigger('click')
    await flushPromises()

    expect(reviewAnnotationSuggestion).toHaveBeenCalledWith(2, 'rejected')
    expect(wrapper.text()).toContain('题库未改动')
  })

  it('双筛选：状态切「已通过」+ 字段切「年份」带参重查', async () => {
    const wrapper = mountPage()
    await flushPromises()

    const selects = wrapper.findAll('select')
    await selects[0].setValue('approved')
    await selects[1].setValue('year')
    await flushPromises()

    expect(listAnnotationSuggestions).toHaveBeenLastCalledWith({ status: 'approved', field_name: 'year' })
  })
})
