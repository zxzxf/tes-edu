import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { permissionDirective } from '../directives/permission'
import { useAuthStore } from '../stores/auth'
import Grading from './Grading.vue'

vi.mock('../api/admin', () => ({
  login: vi.fn(),
  ackGradingFreeze: vi.fn(),
  claimGradingTicket: vi.fn(),
  getGradingMetrics: vi.fn(),
  getGradingTicket: vi.fn(),
  judgeGradingTicket: vi.fn(),
  listGradingFreezes: vi.fn(),
  listGradingTickets: vi.fn(),
  releaseGradingFreeze: vi.fn()
}))

import {
  listGradingTickets, listGradingFreezes, getGradingTicket
} from '../api/admin'

const TICKETS = {
  items: [{
    ticket_id: 1, seq: 1, qtype: 'blank', status: 'pending',
    student_answer: 'x', student_id: 's1', paper_title: '高三数学'
  }],
  counts: { pending: 1, claimed: 0, reviewed: 0 }, total: 1
}

const FREEZES = {
  items: [{
    freeze_id: 1, paper_title: '高三数学', seq: 2, flips: 3,
    affected_count: 1, release_count: 0, status: 'frozen',
    message: '已冻结，请复核标答', answer: 'A', actors: []
  }],
  counts: {}, total: 1
}

const DETAIL = {
  ticket_id: 1, status: 'pending', qtype: 'blank', seq: 1,
  student_id: 's1', student_answer: 'x', stem: '1+1=?',
  options: [], correct_answer: '2', analysis: '', knowledge_points: [],
  answer_values: [], answer_values_effective: true, revisions: [], freeze: null
}

function mountWith(role, perms) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.$patch({ user: { username: 'u', role }, perms, menusLoaded: true })
  return mount(Grading, {
    global: {
      plugins: [pinia],
      directives: { 'has-permission': permissionDirective }
    }
  })
}

async function withDetail(wrapper) {
  await wrapper.find('.col-list .row').trigger('click')
  await flushPromises()
}

const visible = (el) => el && el.element.style.display !== 'none'
const btnByText = (wrapper, text) =>
  wrapper.findAll('button').find((b) => b.text().includes(text))

beforeEach(() => {
  vi.clearAllMocks()
  listGradingTickets.mockResolvedValue(TICKETS)
  listGradingFreezes.mockResolvedValue(FREEZES)
  getGradingTicket.mockResolvedValue(DETAIL)
})

describe('Grading 批改页按钮码（与后端 GradingWriteDep 同口径）', () => {
  it('持码角色可见判定区与冻结处置按钮', async () => {
    const w = mountWith('teacher', ['grading:review'])
    await flushPromises()
    await withDetail(w)

    expect(visible(w.find('.judge'))).toBe(true)
    expect(visible(btnByText(w, '已知悉'))).toBe(true)
    expect(visible(btnByText(w, '解冻'))).toBe(true)
  })

  it('无码角色：待办/留痕仍可见，但批改写入口全部隐藏', async () => {
    const w = mountWith('school', [])
    await flushPromises()
    await withDetail(w)

    expect(w.text()).toContain('高三数学')      // 只读数据照常展示
    expect(visible(w.find('.judge'))).toBe(false)
    expect(visible(btnByText(w, '已知悉'))).toBe(false)
    expect(visible(btnByText(w, '解冻'))).toBe(false)
  })

  it('admin 恒放行（*:* 语义，无需按钮码）', async () => {
    const w = mountWith('admin', [])
    await flushPromises()
    await withDetail(w)

    expect(visible(w.find('.judge'))).toBe(true)
    expect(visible(btnByText(w, '已知悉'))).toBe(true)
  })
})
