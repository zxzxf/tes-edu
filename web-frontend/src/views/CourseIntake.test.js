import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import CourseIntake from './CourseIntake.vue'

vi.mock('../api/admin', () => ({
  createCourseSource: vi.fn(),
  createCrawlJob: vi.fn(),
  deleteCourseSource: vi.fn(),
  getCrawlMetrics: vi.fn(),
  listCourseSources: vi.fn(),
  listCrawlJobs: vi.fn(),
  listKpNodes: vi.fn(),
  parseCourseSource: vi.fn(),
  retryCrawlJob: vi.fn(),
  reviewKpNode: vi.fn(),
  rollbackCrawlRun: vi.fn(),
  runCrawlJob: vi.fn(),
  setCourseSourceStatus: vi.fn(),
  setCrawlJobStatus: vi.fn(),
  upsertKpNodes: vi.fn()
}))

import {
  createCourseSource,
  createCrawlJob,
  deleteCourseSource,
  getCrawlMetrics,
  listCourseSources,
  listCrawlJobs,
  listKpNodes,
  parseCourseSource,
  reviewKpNode,
  runCrawlJob,
  setCourseSourceStatus,
  setCrawlJobStatus,
  upsertKpNodes
} from '../api/admin'

const SOURCE = {
  id: 1,
  name: '人教A版必修一',
  source_type: 'file',
  uri: '/data/math.pdf',
  parser: 'auto',
  status: 'paused'
}
const JOB = {
  id: 7,
  name: '每月新题采集',
  source_id: 1,
  path: 'P2',
  schedule: '0 3 * * *',
  budget_limit: 100,
  spent: 0,
  status: 'active',
  last_paper_id: null
}
const METRICS = {
  runs: 3,
  ok: 2,
  failed: 1,
  stopped: 0,
  success_rate: 0.6667,
  inserted: 8,
  duplicated: 2,
  dedup_rate: 0.2,
  recent: [
    {
      id: 11,
      job_id: 7,
      status: 'ok',
      fetched: 10,
      inserted: 8,
      duplicated: 2,
      failed: 0,
      paper_id: 21,
      started_at: null,
      finished_at: null,
      error: null
    }
  ]
}

function mountPage() {
  return mount(CourseIntake)
}

function buttonByText(wrapper, text) {
  const btn = wrapper.findAll('button').find((b) => b.text() === text)
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn
}

beforeEach(() => {
  vi.clearAllMocks()
  listCourseSources.mockResolvedValue([SOURCE])
  listCrawlJobs.mockResolvedValue([JOB])
  getCrawlMetrics.mockResolvedValue(METRICS)
  listKpNodes.mockResolvedValue([])
})

describe('CourseIntake 课程采集页（M5-1/2/3/4/6、M3-8、M4-4）', () => {
  it('进入页面加载课程源、任务、指标与知识点候选', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(listCourseSources).toHaveBeenCalled()
    expect(listCrawlJobs).toHaveBeenCalled()
    expect(getCrawlMetrics).toHaveBeenCalled()
    expect(listKpNodes).toHaveBeenCalledWith('draft')
    expect(wrapper.text()).toContain('人教A版必修一')
    expect(wrapper.text()).toContain('每月新题采集')
    expect(wrapper.text()).toContain('成功率 66.7%')
    expect(wrapper.text()).toContain('去重率 20.0%')
  })

  it('新增课程源后清空输入并刷新列表', async () => {
    createCourseSource.mockResolvedValue({ id: 2 })
    listCourseSources.mockResolvedValueOnce([SOURCE]).mockResolvedValueOnce([SOURCE, { ...SOURCE, id: 2 }])
    const wrapper = mountPage()
    await flushPromises()

    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('新教材')
    await inputs[1].setValue('https://example.com/book')
    await buttonByText(wrapper, '新增').trigger('click')
    await flushPromises()

    expect(createCourseSource).toHaveBeenCalledTimes(1)
    const payload = createCourseSource.mock.calls[0][0]
    expect(payload.name).toBe('新教材')
    expect(payload.uri).toBe('https://example.com/book')
    expect(payload.source_type).toBe('file')
    expect(listCourseSources).toHaveBeenCalledTimes(2)
    expect(inputs[0].element.value).toBe('')
    expect(wrapper.text()).toContain('课程源已创建')
  })

  it('暂停/启用切换用相反状态调用后端', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '启用').trigger('click')
    await flushPromises()
    expect(setCourseSourceStatus).toHaveBeenCalledWith(1, 'active')
  })

  it('暂停的采集任务点「启用定时」会调后端并提示下次时间', async () => {
    listCrawlJobs.mockResolvedValue([{ ...JOB, status: 'paused' }])
    setCrawlJobStatus.mockResolvedValue({ id: 7, status: 'active', next_run_at: '2026-03-02T03:00:00' })
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '启用定时').trigger('click')
    await flushPromises()

    expect(setCrawlJobStatus).toHaveBeenCalledWith(7, 'active')
    expect(listCrawlJobs).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('2026-03-02T03:00:00')
  })

  it('删除课程源后刷新列表', async () => {
    deleteCourseSource.mockResolvedValue({ ok: true })
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '删除').trigger('click')
    await flushPromises()
    expect(deleteCourseSource).toHaveBeenCalledWith(1)
    expect(listCourseSources).toHaveBeenCalledTimes(2)
  })

  it('新建采集任务带上 P1/P2 路径与预算', async () => {
    createCrawlJob.mockResolvedValue({ id: 8 })
    const wrapper = mountPage()
    await flushPromises()

    const inputs = wrapper.findAll('input')
    await inputs[2].setValue('整卷采集')
    await inputs[4].setValue('50')
    await wrapper.findAll('select')[2].setValue('P1')
    await buttonByText(wrapper, '新建').trigger('click')
    await flushPromises()

    const payload = createCrawlJob.mock.calls[0][0]
    expect(payload.name).toBe('整卷采集')
    expect(payload.path).toBe('P1')
    expect(payload.budget_limit).toBe(50)
    expect(wrapper.text()).toContain('采集任务已创建')
  })

  it('P1 执行后提示产出卷编号并刷新指标', async () => {
    runCrawlJob.mockResolvedValue({ ...JOB, last_paper_id: 33 })
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '执行一次').trigger('click')
    await flushPromises()

    expect(runCrawlJob).toHaveBeenCalledWith(7)
    expect(getCrawlMetrics).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('本次产出卷 #33')
    expect(wrapper.text()).toContain('草稿')
  })

  it('P2 执行明确说明只入题库未成卷', async () => {
    runCrawlJob.mockResolvedValue({ ...JOB, last_paper_id: null })
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '执行一次').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('P2 只入题库')
  })

  it('解析大纲后展示章节与候选知识点数量', async () => {
    parseCourseSource.mockResolvedValue({
      chapter_count: 1,
      point_count: 2,
      subject: '数学',
      chapters: []
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('textarea').setValue('第一章 数列')
    await buttonByText(wrapper, '解析').trigger('click')
    await flushPromises()

    expect(parseCourseSource).toHaveBeenCalledWith({
      text: '第一章 数列',
      source_type: 'textbook'
    })
    expect(wrapper.text()).toContain('解析出 1 章 / 2 个候选知识点')
  })

  it('导入解析结果时把章节树拍平成带 parent_code 的候选', async () => {
    parseCourseSource.mockResolvedValue({
      chapter_count: 1,
      point_count: 2,
      subject: '数学',
      chapters: [
        {
          code: 'C1',
          title: '数列',
          children: [{ code: 'C1-1', name: '等差数列', children: [] }]
        }
      ]
    })
    upsertKpNodes.mockResolvedValue({ items: [] })
    listKpNodes.mockResolvedValueOnce([]).mockResolvedValueOnce([
      { code: 'C1', name: '数列', parent_code: null, source: 'crawled', status: 'draft' }
    ])
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.find('textarea').setValue('第一章 数列')
    await buttonByText(wrapper, '解析').trigger('click')
    await flushPromises()
    await buttonByText(wrapper, '导入知识点候选').trigger('click')
    await flushPromises()

    const items = upsertKpNodes.mock.calls[0][0]
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ code: 'C1', name: '数列', parent_code: null, source: 'crawled' })
    expect(items[1]).toMatchObject({ code: 'C1-1', name: '等差数列', parent_code: 'C1' })
    expect(wrapper.text()).toContain('已导入 2 个候选')
  })

  it('审核通过调用 published，并刷新当前筛选列表', async () => {
    listKpNodes.mockResolvedValue([
      { code: 'C1-1', name: '等差数列', parent_code: 'C1', source: 'crawled', status: 'draft' }
    ])
    const wrapper = mountPage()
    await flushPromises()

    await buttonByText(wrapper, '通过').trigger('click')
    await flushPromises()
    expect(reviewKpNode).toHaveBeenCalledWith('C1-1', 'published')
    expect(listKpNodes).toHaveBeenLastCalledWith('draft')
  })

  it('后端拒绝时展示 detail，不误报成功', async () => {
    createCourseSource.mockRejectedValue({
      response: { data: { detail: '该来源命中合规黑名单，禁止采集' } }
    })
    const wrapper = mountPage()
    await flushPromises()

    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('违规源')
    await inputs[1].setValue('https://copyright-reserved.example/x')
    await buttonByText(wrapper, '新增').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('命中合规黑名单')
    expect(wrapper.text()).not.toContain('课程源已创建')
  })
})
