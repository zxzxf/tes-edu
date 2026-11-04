import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PaperDispatch from './PaperDispatch.vue'

const { push, routeQuery } = vi.hoisted(() => ({ push: vi.fn(), routeQuery: { value: {} } }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery.value }),
  useRouter: () => ({ push }),
}))
vi.mock('../api/admin', () => ({
  listPapers: vi.fn(),
  getPaper: vi.fn(),
  publishPaper: vi.fn(),
  assignPaper: vi.fn(),
  getAnswerChecks: vi.fn(),
  runAnswerChecks: vi.fn(),
  confirmAnswerChecks: vi.fn(),
  copyPaper: vi.fn(),
  getPaperGraph: vi.fn(),
  getPaperSolutions: vi.fn(),
}))

import {
  listPapers,
  getPaper,
  getAnswerChecks,
  runAnswerChecks,
  confirmAnswerChecks,
  publishPaper,
  assignPaper,
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
  return mount(PaperDispatch)
}

beforeEach(() => {
  vi.clearAllMocks()
  routeQuery.value = {}
  push.mockResolvedValue(undefined)
  listPapers.mockResolvedValue(PAPERS)
  getPaper.mockResolvedValue(PAPER)
  getAnswerChecks.mockResolvedValue(CHECKS)
  runAnswerChecks.mockResolvedValue(CHECKS)
  confirmAnswerChecks.mockResolvedValue({ ...CHECKS, summary: { ...CHECKS.summary, ok: true } })
  publishPaper.mockResolvedValue({ ...PAPER, published: true })
  assignPaper.mockResolvedValue({ assignment_id: 88 })
})

describe('PaperDispatch.vue 列表与答案双源校验（M2-9 / G1）', () => {
  it('进页面默认展开列表第一份卷并展示每题校验结论', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listPapers).toHaveBeenCalled()
    expect(getPaper).toHaveBeenCalledWith(9)
    expect(getAnswerChecks).toHaveBeenCalledWith(9)
    expect(wrapper.text()).toContain('答案校验')
    expect(wrapper.text()).toContain('通过 1/2')
    expect(wrapper.text()).toContain('代入检验通过')
    expect(wrapper.text()).toContain('待确认')
    const rows = wrapper.findAll('.ac-row')
    expect(rows.length).toBe(2)
    expect(rows[0].find('.ac-act').exists()).toBe(false)
    expect(rows[1].find('.ac-act').exists()).toBe(true)
  })

  it('「全部确认」把本卷待确认题一次性提交', async () => {
    const wrapper = mountPage()
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
    publishPaper.mockRejectedValueOnce({
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
    getAnswerChecks.mockClear()
    const publishBtn = wrapper.findAll('button').find((b) => b.text() === '校验并发布')
    await publishBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('发布被拦截')
    expect(wrapper.text()).toContain('第 2 题：需教师确认')
    expect(getAnswerChecks).toHaveBeenCalled()
  })

  it('?paper=34 时自动选中指定卷（从自动组卷跳来）', async () => {
    routeQuery.value = { paper: '34' }
    listPapers.mockResolvedValue([
      { ...PAPERS[0], id: 34, title: '新生成的卷' },
      PAPERS[0],
    ])
    getPaper.mockResolvedValue({ ...PAPER, id: 34, title: '新生成的卷' })
    const wrapper = mountPage()
    await flushPromises()
    expect(getPaper).toHaveBeenCalledWith(34)
    expect(wrapper.text()).toContain('新生成的卷')
  })
})

describe('PaperDispatch.vue 发布与下发', () => {
  it('未发布卷只显示「校验并发布」，发布成功后提示可下发', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.findAll('button').some((b) => b.text() === '下发')).toBe(false)
    const btn = wrapper.findAll('button').find((b) => b.text() === '校验并发布')
    await btn.trigger('click')
    await flushPromises()
    expect(publishPaper).toHaveBeenCalledWith(9)
    expect(wrapper.text()).toContain('已发布，可下发给学生')
  })

  it('已发布卷可下发到指定学生，调用 assignPaper 并回报 assignment_id', async () => {
    listPapers.mockResolvedValue([{ ...PAPERS[0], published: true }])
    getPaper.mockResolvedValue({ ...PAPER, published: true })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('下发到')
    await wrapper.find('select').setValue('student')
    await wrapper.find('input[placeholder^="学生ID"]').setValue('student@tes.edu')
    const btn = wrapper.findAll('button').find((b) => b.text() === '下发')
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    await flushPromises()

    expect(assignPaper).toHaveBeenCalledWith(9, {
      target_type: 'student',
      target_grade: null,
      target_student: 'student@tes.edu'
    })
    expect(wrapper.text()).toContain('assignment_id=88')
  })

  it('点「去自动组卷」回到组卷页', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const btn = wrapper.findAll('button').find((b) => b.text() === '去自动组卷')
    await btn.trigger('click')
    expect(push).toHaveBeenCalledWith('/exam')
  })
})
