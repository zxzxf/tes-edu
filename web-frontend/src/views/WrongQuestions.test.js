import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import WrongQuestions from './WrongQuestions.vue'
import { DEMO_STUDENT_ID } from '../api/admin'

vi.mock('../api/admin', () => ({
  listWrongQuestions: vi.fn(),
  DEMO_STUDENT_ID: 'student@tes.edu',
}))

import { listWrongQuestions } from '../api/admin'

const ROWS = [
  {
    id: 1,
    student_id: 'student@tes.edu',
    question: '1 + 1 等于多少？',
    wrong_answer: '3',
    correct_answer: '2',
    knowledge_point: '加法',
    question_type: 'choice',
    error_type_label: '计算错误',
    wrong_step_no: 1,
  },
  {
    // 线上 46 条未软删错题里 12 条标答为 NULL，曾让整个列表 500
    id: 2,
    student_id: 'student@tes.edu',
    question: '解方程 2x = 4',
    wrong_answer: 'x = 1',
    correct_answer: '',
    knowledge_point: '',
    question_type: 'blank',
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  listWrongQuestions.mockResolvedValue(ROWS)
})

const mountPage = () => mount(WrongQuestions)

describe('WrongQuestions 错题本页（2026-11-14 列表看不到数据）', () => {
  it('挂载即加载，默认查真实演示学生而不是空账号 student_001', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listWrongQuestions).toHaveBeenCalledWith(DEMO_STUDENT_ID)
    expect(DEMO_STUDENT_ID).not.toBe('student_001')
    expect(wrapper.find('input').element.value).toBe(DEMO_STUDENT_ID)
    expect(wrapper.text()).toContain('1 + 1 等于多少？')
    expect(wrapper.text()).toContain('共 2 条')
  })

  it('展示后端回传的题型，标答为空显示占位而不是 null', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('选择题')
    expect(wrapper.text()).toContain('填空题')
    expect(wrapper.text()).toContain('—')
    expect(wrapper.text()).not.toContain('null')
  })

  it('403 必须如实提示，不能伪装成「该学生暂无错题」', async () => {
    listWrongQuestions.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } },
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('权限不足（403）：当前角色无权查看错题本')
    expect(wrapper.text()).not.toContain('该学生暂无错题')
    expect(wrapper.find('.err').exists()).toBe(true)
  })

  it('500 也要显示错误码，不再变成 unhandled rejection 后的空态', async () => {
    listWrongQuestions.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'Internal Server Error' } },
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败（500）')
    expect(wrapper.text()).not.toContain('该学生暂无错题')
  })

  it('真的没数据时才显示空态', async () => {
    listWrongQuestions.mockResolvedValueOnce([])
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('该学生暂无错题')
    expect(wrapper.find('.err').exists()).toBe(false)
  })

  it('输入其他学生 ID 后点「查询」按输入值重新拉取', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('input').setValue('u_admin')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(listWrongQuestions).toHaveBeenLastCalledWith('u_admin')
    expect(wrapper.find('.err').exists()).toBe(false)
  })
})
