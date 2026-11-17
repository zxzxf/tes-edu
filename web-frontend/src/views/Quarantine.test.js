import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Quarantine from './Quarantine.vue'

vi.mock('../api/admin', () => ({
  getQuarantineStats: vi.fn(),
  listQuarantineItems: vi.fn(),
  resolveQuarantineItem: vi.fn(),
}))

import { getQuarantineStats, listQuarantineItems, resolveQuarantineItem } from '../api/admin'

const STATS = {
  quarantine_total: 2,
  flagged_total: 1,
  archived_total: 3,
  by_gate: { dedup_text: 2, answer_out_of_range: 1 },
  by_chain: { crawl: 2, teacher: 1 },
  gate_labels: { dedup_text: '题干指纹重复', answer_out_of_range: '选项越界' },
}

const ITEMS = [
  {
    id: 7, subject: '数学', grade: '高三', type: '选择',
    stem: '设集合 A={x|-2<x<4}，B={2,3,4,5}，则 A∩B=( )', answer: 'H',
    status: 'quarantine', source_tag: 'auto_crawl:guard_quarantine', base_tag: 'auto_crawl',
    chain: 'crawl', guard_gate: 'dedup_text', guard_gate_label: '题干指纹重复',
    guard_reason: '题干文本指纹重复（OCR/标点差异）：abc123', dup_of: 5,
  },
]

function mountPage() {
  return mount(Quarantine)
}

function btn(wrapper, text) {
  return wrapper.findAll('button').find((b) => b.text() === text)
}

beforeEach(() => {
  vi.clearAllMocks()
  getQuarantineStats.mockResolvedValue(STATS)
  listQuarantineItems.mockResolvedValue(ITEMS)
  resolveQuarantineItem.mockResolvedValue({ id: 7, status: 'draft', source_tag: 'auto_crawl:guard_released' })
})

describe('Quarantine 异常队列页（T-CAP4）', () => {
  it('挂载即加载看板与隔离队列，展示原因/重复指向/来源链路', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(getQuarantineStats).toHaveBeenCalled()
    expect(listQuarantineItems).toHaveBeenCalledWith({ kind: 'quarantine' })

    const text = wrapper.text()
    expect(text).toContain('隔离待处置')
    expect(text).toContain('2')
    expect(text).toContain('题干指纹重复')
    expect(text).toContain('与 #5 重复')
    expect(text).toContain('采集')
    expect(text).toContain('共 1 条')
  })

  it('403 如实提示，不伪装成「没有待处置」', async () => {
    listQuarantineItems.mockRejectedValueOnce({
      response: { status: 403, data: { detail: 'insufficient role' } },
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('权限不足（403）')
    expect(wrapper.text()).not.toContain('当前没有待处置的隔离题')
  })

  it('切 tab 到「打标待人工」带 kind 重查', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '打标待人工').trigger('click')
    await flushPromises()

    expect(listQuarantineItems).toHaveBeenLastCalledWith({ kind: 'flagged' })
  })

  it('按闸筛选带 gate 参数重查', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('select').setValue('dedup_text')
    await flushPromises()

    expect(listQuarantineItems).toHaveBeenLastCalledWith({ kind: 'quarantine', gate: 'dedup_text' })
  })

  it('「放行」直接调 release 并刷新列表+看板', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '放行').trigger('click')
    await flushPromises()

    expect(resolveQuarantineItem).toHaveBeenCalledWith(7, 'release')
    expect(listQuarantineItems).toHaveBeenCalledTimes(2)
    expect(getQuarantineStats).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('进入常规审核')
  })

  it('「发布」需二次确认：第一次只出确认，点「确认」才真正调用', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '发布').trigger('click')
    await flushPromises()
    expect(resolveQuarantineItem).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('确认发布进卷池？')

    await btn(wrapper, '确认').trigger('click')
    await flushPromises()
    expect(resolveQuarantineItem).toHaveBeenCalledWith(7, 'publish')
    expect(wrapper.text()).toContain('进入卷池')
  })

  it('归档确认可取消，不误调用接口', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '归档').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('确认归档移出队列？')

    await btn(wrapper, '取消').trigger('click')
    await flushPromises()
    expect(resolveQuarantineItem).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('确认归档移出队列？')
  })

  it('409（状态已变化）如实提示，不静默失败', async () => {
    resolveQuarantineItem.mockRejectedValueOnce({
      response: { status: 409, data: { detail: '该题不在异常队列（可能已处置）' } },
    })
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '放行').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('处置失败')
    expect(wrapper.text()).toContain('该题不在异常队列')
  })

  it('已归档视图只读：不出现处置按钮', async () => {
    listQuarantineItems.mockResolvedValue([])
    const wrapper = mountPage()
    await flushPromises()

    await btn(wrapper, '已归档').trigger('click')
    await flushPromises()

    expect(listQuarantineItems).toHaveBeenLastCalledWith({ kind: 'archived' })
    expect(btn(wrapper, '放行')).toBeUndefined()
    expect(btn(wrapper, '归档')).toBeUndefined()
  })
})
