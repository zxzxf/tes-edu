import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Exam from './Exam.vue'

const { push } = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))
vi.mock('../api/admin', () => ({
  composePaper: vi.fn(),
  createManualPaper: vi.fn(),
  bindQuestionKp: vi.fn(),
  backfillQuestionKp: vi.fn(),
  getQuestionKpCoverage: vi.fn(),
  listReviewQuestions: vi.fn(),
  llmGeneratePaper: vi.fn(),
  reviewQuestion: vi.fn(),
  searchQuestions: vi.fn(),
}))

import {
  composePaper,
  listReviewQuestions,
  reviewQuestion,
} from '../api/admin'

function mountPage() {
  return mount(Exam)
}

beforeEach(() => {
  vi.clearAllMocks()
  push.mockResolvedValue(undefined)
  composePaper.mockResolvedValue({ id: 34, title: '新生成的卷' })
  listReviewQuestions.mockResolvedValue([
    {
      id: 12, stem: '待审核题干', answer: 'A', status: 'draft',
      type: '单选', source_tag: 'import', options: [{ label: 'A', content: '一' }]
    }
  ])
  reviewQuestion.mockResolvedValue({ id: 12, status: 'published' })
})

describe('Exam.vue 题库审核（M0-2）', () => {
  it('教师能查看草稿题并审核通过', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const open = wrapper.findAll('button').find((b) => b.text() === '题库审核')
    await open.trigger('click')
    await flushPromises()
    expect(listReviewQuestions).toHaveBeenCalledWith('draft')
    expect(wrapper.text()).toContain('待审核题干')
    expect(wrapper.text()).toContain('A. 一')
    await wrapper.find('.review-row .approve').trigger('click')
    await flushPromises()
    expect(reviewQuestion).toHaveBeenCalledWith(12, 'published')
  })
})

// 2026-11-04：试卷列表/发布/下发整体拆到 /papers（独立菜单「试卷下发」），
// 本页只负责生成，组卷成功后直接把教师带到新页继续发布/下发。
describe('Exam.vue 拆页后的跳转', () => {
  it('顶部提示标明入口，点「去试卷下发」跳到 /papers', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.text()).toContain('试卷下发')
    expect(wrapper.text()).toContain('本页只负责生成试卷')
    const btn = wrapper.findAll('button').find((b) => b.text() === '去试卷下发')
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    expect(push).toHaveBeenCalledWith('/papers')
  })

  it('生成试卷后带 paper id 跳到试卷下发页', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const btn = wrapper.findAll('button').find((b) => b.text() === '生成试卷')
    await btn.trigger('click')
    await flushPromises()
    expect(composePaper).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith({ path: '/papers', query: { paper: 34 } })
    expect(wrapper.text()).toContain('正在跳转到试卷下发')
  })

  it('本页不再渲染试卷列表与发布/下发按钮', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.find('.papers').exists()).toBe(false)
    expect(wrapper.findAll('button').some((b) => b.text() === '校验并发布')).toBe(false)
    expect(wrapper.findAll('button').some((b) => b.text() === '下发')).toBe(false)
  })
})
