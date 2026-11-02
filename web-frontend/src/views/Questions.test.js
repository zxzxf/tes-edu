import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Questions from './Questions.vue'

vi.mock('../api/admin', () => ({
  createQuestion: vi.fn(),
  createQuestionsBatch: vi.fn(),
  listQuestions: vi.fn()
}))

import { createQuestion, createQuestionsBatch, listQuestions } from '../api/admin'

const OK_RESULT = {
  received: 1,
  created: 1,
  duplicated: 0,
  items: [{ id: 1, content_hash: 'h', status: 'draft', duplicated: false }],
  embedding: { ok: true, model: 'qwen3.7-text-embedding', source: 'tenant', reason: '', written: 1 }
}

function mountPage() {
  return mount(Questions, { global: { stubs: { 'router-link': true } } })
}

async function fillStem(wrapper, stem = '已知 f(x)=x^2，求 f(2)') {
  await wrapper.find('textarea').setValue(stem)
}

beforeEach(() => {
  vi.clearAllMocks()
  listQuestions.mockResolvedValue([])
})

describe('Questions 题库录题页（M0-1）', () => {
  it('题干为空时不发请求，直接提示', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('.tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createQuestion).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('题干不能为空')
  })

  it('提交时把选项按行拆成数组，并带上 qtype/choice_type', async () => {
    createQuestion.mockResolvedValue(OK_RESULT)
    const wrapper = mountPage()
    await flushPromises()

    await fillStem(wrapper)
    const areas = wrapper.findAll('textarea')
    await areas[1].setValue('A. 1\nB. 4\nC. 8\nD. 16')
    const answerInput = wrapper.findAll('input').find((i) => i.element.value === '')
    await answerInput.setValue('B')

    await wrapper.find('.tes-btn-primary').trigger('click')
    await flushPromises()

    expect(createQuestion).toHaveBeenCalledTimes(1)
    const payload = createQuestion.mock.calls[0][0]
    expect(payload.qtype).toBe('choice')
    expect(payload.choice_type).toBe('single')
    expect(payload.options).toEqual(['A. 1', 'B. 4', 'C. 8', 'D. 16'])
    expect(payload.stem).toContain('f(2)')
    expect(wrapper.text()).toContain('入库 1 题')
  })

  it('向量未生成时明确告知「题目已入库待审核」，不显示为失败', async () => {
    createQuestion.mockResolvedValue({
      ...OK_RESULT,
      embedding: {
        ok: false,
        model: 'qwen3.7-text-embedding',
        source: 'tenant',
        reason: 'embedding 调用失败：被限流 (429)，配额不足',
        written: 0
      }
    })
    const wrapper = mountPage()
    await flushPromises()

    await fillStem(wrapper)
    await wrapper.find('.tes-btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('向量未生成')
    expect(wrapper.text()).toContain('429')
    expect(wrapper.text()).toContain('题目已入库')
  })

  it('批量 JSON 非法时提示解析失败且不请求后端', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.findAll('textarea').at(-1).setValue('{不是数组}')
    const batchBtn = wrapper.findAll('.tes-btn-primary').at(-1)
    await batchBtn.trigger('click')
    await flushPromises()

    expect(createQuestionsBatch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('批量内容解析失败')
  })

  it('批量提交把 JSON 数组原样交给后端', async () => {
    createQuestionsBatch.mockResolvedValue({ ...OK_RESULT, received: 1 })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.findAll('textarea').at(-1).setValue(
      '[{"subject":"数学","qtype":"blank","stem":"1+1=?","answer":"2"}]'
    )
    await wrapper.findAll('.tes-btn-primary').at(-1).trigger('click')
    await flushPromises()

    expect(createQuestionsBatch).toHaveBeenCalledTimes(1)
    expect(createQuestionsBatch.mock.calls[0][0]).toHaveLength(1)
  })
})
