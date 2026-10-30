import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Exam from './Exam.vue'

vi.mock('../api/admin', () => ({
  listPapers: vi.fn(),
  getPaper: vi.fn(),
  composePaper: vi.fn(),
  publishPaper: vi.fn(),
  assignPaper: vi.fn(),
  getAnswerChecks: vi.fn(),
  runAnswerChecks: vi.fn(),
  confirmAnswerChecks: vi.fn(),
  listReviewQuestions: vi.fn(),
  reviewQuestion: vi.fn()
}))

import {
  listPapers,
  getPaper,
  getAnswerChecks,
  confirmAnswerChecks,
  publishPaper,
  listReviewQuestions,
  reviewQuestion
} from '../api/admin'

const PAPERS = [
  {
    id: 9,
    title: '高三·数学·纯填空题卷（2题）',
    published: false,
    question_count: 2,
    auto_gradeable_count: 1,
    manual_required_count: 1
  }
]

const PAPER = {
  id: 9,
  title: '高三·数学·纯填空题卷（2题）',
  paper_type: 'pure_blank',
  published: false,
  question_count: 2,
  auto_gradeable_count: 1,
  manual_required_count: 1,
  questions: [
    { seq: 1, qtype: 'blank', stem: '已知 x^2-2x-3=0，求 x', answer: '3', options: [], answer_values: [] },
    { seq: 2, qtype: 'blank', stem: '求下列各式的值', answer: '7', options: [], answer_values: [] }
  ]
}

const CHECKS = {
  paper_id: 9,
  published: false,
  summary: { total: 2, passed: 1, conflict: 0, unverified: 1, by_status: {}, ok: false },
  items: [
    {
      seq: 1,
      qtype: 'blank',
      answer: '3',
      status: 'mechanical_pass',
      status_label: '代入检验通过',
      detail: '把答案 3 代入 x^2-2x-3=0 成立',
      suspect: false
    },
    {
      seq: 2,
      qtype: 'blank',
      answer: '7',
      status: 'unverified',
      status_label: '待确认',
      detail: '无第二来源、无法机械验证：需教师确认后才可发布',
      suspect: false
    }
  ]
}

function mountPage() {
  return mount(Exam, { global: { stubs: {} } })
}

beforeEach(() => {
  vi.clearAllMocks()
  listPapers.mockResolvedValue(PAPERS)
  getPaper.mockResolvedValue(PAPER)
  getAnswerChecks.mockResolvedValue(CHECKS)
  confirmAnswerChecks.mockResolvedValue({ ...CHECKS, summary: { ...CHECKS.summary, ok: true } })
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

describe('Exam.vue 答案双源校验（M2-9 / G1）', () => {
  it('选中试卷后展示每题的校验结论与汇总', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.paper').trigger('click')
    await flushPromises()

    expect(getAnswerChecks).toHaveBeenCalledWith(9)
    expect(wrapper.text()).toContain('答案校验')
    expect(wrapper.text()).toContain('通过 1/2')
    expect(wrapper.text()).toContain('代入检验通过')
    expect(wrapper.text()).toContain('待确认')
    // 可代入的那道题没有确认按钮，待确认的那道才有
    const rows = wrapper.findAll('.ac-row')
    expect(rows.length).toBe(2)
    expect(rows[0].find('.ac-act').exists()).toBe(false)
    expect(rows[1].find('.ac-act').exists()).toBe(true)
  })

  it('「全部确认」把本卷待确认题一次性提交', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.paper').trigger('click')
    await flushPromises()

    const btn = wrapper.findAll('button').find((b) => b.text() === '全部确认')
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    await flushPromises()

    expect(confirmAnswerChecks).toHaveBeenCalledWith(9, { seqs: [], answers: {}, comment: '' })
  })

  it('单题确认可顺手修正答案', async () => {
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.paper').trigger('click')
    await flushPromises()

    const row = wrapper.findAll('.ac-row')[1]
    await row.find('.ac-act input').setValue('9')
    await row.find('.ac-act button').trigger('click')
    await flushPromises()

    expect(confirmAnswerChecks).toHaveBeenCalledWith(9, {
      seqs: [2],
      answers: { 2: '9' },
      comment: ''
    })
  })

  it('发布被 G1 拦住时展示拦截明细并刷新校验', async () => {
    publishPaper.mockRejectedValue({
      response: {
        data: {
          detail: {
            code: 'ANSWER_UNVERIFIED',
            message: '1 道题的答案未通过双源校验',
            violations: [
              { seq: 2, qtype: 'blank', code: 'ANSWER_UNVERIFIED', message: '第 2 题：需教师确认' }
            ]
          }
        }
      }
    })
    const wrapper = mountPage()
    await flushPromises()
    await wrapper.find('.paper').trigger('click')
    await flushPromises()
    getAnswerChecks.mockClear()

    const publishBtn = wrapper.findAll('button').find((b) => b.text() === '校验并发布')
    await publishBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('发布被拦截')
    expect(wrapper.text()).toContain('第 2 题：需教师确认')
    expect(getAnswerChecks).toHaveBeenCalled()   // 拦住后自动刷新校验面板
  })
})
